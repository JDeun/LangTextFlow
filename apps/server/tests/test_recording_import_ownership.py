"""Actual request-stream cancellation, private FD ownership, WAV and SQLite import."""
from __future__ import annotations

import asyncio
import hashlib
import io
import threading
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import ClientDisconnect, Request

from langtextflow import post_event_api, recording_import
from langtextflow.config import Settings
from langtextflow.models import SessionState, StartSessionRequest
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.session_repository import SessionRepository


@pytest.fixture
def importer(tmp_path, monkeypatch):
    history = SessionRepository(str(tmp_path / "history.db"))
    history.initialize()
    history.create_session(SessionState(
        session_id="owner", join_code="TEST", started_at=datetime.now(UTC),
    ), StartSessionRequest())
    history.mark_ended("owner")
    archive = PostEventArchive(str(history.database_path))
    archive.initialize()
    app = FastAPI()
    post_event_api.install_post_event_routes(
        app, SimpleNamespace(history=history, state=SessionState()), archive,
        Settings(database_path=str(history.database_path), storage_reserve_mb=64), lambda _: None,
    )
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", "") == "/api/v1/history/{session_id}/recordings")
    owners = []
    constructor = post_event_api.RecordingImport

    def capture(*args):
        owned = constructor(*args)
        owners.append(owned)
        return owned

    monkeypatch.setattr(post_event_api, "RecordingImport", capture)
    return archive, history, endpoint, owners


def wav_bytes(*, stereo=False, extra=False):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setparams((2 if stereo else 1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 320 * (2 if stereo else 1))
    data = buf.getvalue()
    if extra:
        data = data[:12] + b"JUNK\x08\0\0\0metadata" + data[12:]
        data = data[:4] + (len(data) - 8).to_bytes(4, "little") + data[8:]
    return data


def request_for(data, *, disconnected=False):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": data, "more_body": disconnected}
        return {"type": "http.disconnect"}

    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [(b"content-type", b"audio/wav")]}, receive)


@pytest.mark.asyncio
@pytest.mark.parametrize("stereo,extra", [(False, False), (True, True)])
async def test_import_promotes_real_wav_with_hash_and_durable_owner(importer, stereo, extra):
    archive, _, endpoint, owners = importer
    data = wav_bytes(stereo=stereo, extra=extra)
    result = await endpoint(request_for(data), "owner")
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["channels"] == (2 if stereo else 1) and result["duration_seconds"] == .02
    assert archive.recording_path("owner", result["recording_id"]).read_bytes() == data
    assert owners[0]._stream is None and not owners[0].partial.exists()
    with archive._connect() as conn:
        row = conn.execute("SELECT * FROM recording_journal").fetchone()
        assert row["registered"] == 1 and row["owner_session_id"] == "owner"


@pytest.mark.asyncio
async def test_import_sql_fault_after_rename_preserves_verified_recoverable_file(importer):
    archive, _, endpoint, owners = importer
    with archive._connect() as conn:
        conn.execute("CREATE TRIGGER interrupt_import BEFORE INSERT ON session_recordings "
                     "BEGIN SELECT RAISE(ABORT, 'crash window'); END")
    with pytest.raises(HTTPException) as error:
        await endpoint(request_for(wav_bytes()), "owner")
    assert error.value.status_code == 422
    assert owners[0].final.exists() and owners[0]._stream is None
    with archive._connect() as conn:
        row = dict(conn.execute("SELECT * FROM recording_journal").fetchone())
        conn.execute("DROP TRIGGER interrupt_import")
    assert archive.recordings("owner") == []
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert archive.recordings("owner")[0]["recording_id"] == row["recording_id"]


@pytest.mark.asyncio
async def test_client_disconnect_discards_owned_partial_and_closes_fd(importer):
    archive, _, endpoint, owners = importer
    with pytest.raises(ClientDisconnect):
        await endpoint(request_for(wav_bytes()[:100], disconnected=True), "owner")
    assert owners[0]._stream is None and not list(archive.audio_root.iterdir())
    assert archive.recordings("owner") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["open", "write", "promotion"])
async def test_cancelled_native_import_io_never_races_close_or_leaks_partial(
    importer, monkeypatch, boundary,
):
    archive, _, endpoint, owners = importer
    entered, release = threading.Event(), threading.Event()
    raw_streams = []
    real_fdopen = recording_import.os.fdopen
    original_verify = archive.recording_journal._verify

    class SlowStream:
        def __init__(self, stream):
            self.stream = stream

        def write(self, data):
            entered.set()
            assert release.wait(3)
            assert not self.stream.closed
            return self.stream.write(data)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def fdopen(*args):
        stream = real_fdopen(*args)
        raw_streams.append(stream)
        if boundary == "open":
            entered.set()
            assert release.wait(3)
        return SlowStream(stream) if boundary == "write" else stream

    def verify(row, path, stop):
        entered.set()
        assert release.wait(3)
        return original_verify(row, path, stop)

    monkeypatch.setattr(recording_import.os, "fdopen", fdopen)
    if boundary == "promotion":
        monkeypatch.setattr(archive.recording_journal, "_verify", verify)
    task = asyncio.create_task(endpoint(request_for(wav_bytes()), "owner"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        if boundary in {"open", "write"}:
            assert not raw_streams[0].closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert all(stream.closed for stream in raw_streams)
    assert owners[0]._stream is None and not list(archive.audio_root.iterdir())
    assert archive.recordings("owner") == []
    with archive._connect() as conn:
        assert conn.execute("SELECT count(*) FROM recording_journal").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_deleted_session_during_upload_cannot_leave_or_register_audio(importer, monkeypatch):
    archive, history, endpoint, owners = importer
    original = recording_import.RecordingImport.write

    def delete_after_write(owned, data):
        original(owned, data)
        history.delete_session("owner")

    monkeypatch.setattr(recording_import.RecordingImport, "write", delete_after_write)
    with pytest.raises(HTTPException) as error:
        await endpoint(request_for(wav_bytes()), "owner")
    assert error.value.status_code == 422
    assert owners[0]._stream is None and not list(archive.audio_root.iterdir())
    assert archive.recordings("owner") == []
