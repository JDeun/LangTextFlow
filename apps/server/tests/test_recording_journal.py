"""Real WAV/SQLite/file recovery, including crash windows and late native open."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import struct
import threading
import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from langtextflow.config import Settings
from langtextflow.models import SessionState, StartSessionRequest
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.recording_journal import MAX_MEDIA_BYTES, RecoveryStopped
from langtextflow.recording_writer import SessionRecordingWriter
from langtextflow.runtime import CaptionRuntime
from langtextflow.session_repository import SessionRepository


@pytest.fixture
def stores(tmp_path):
    db = str(tmp_path / "history.db")
    history = SessionRepository(db)
    history.initialize()
    history.create_session(SessionState(
        session_id="owner", join_code="TEST", started_at=datetime.now(UTC),
    ), StartSessionRequest())
    history.mark_ended("owner")
    archive = PostEventArchive(db)
    archive.initialize()
    return archive, history


def pending(archive, *, promoted=True):
    filename = f"{uuid4().hex}.wav"
    partial = archive.audio_root / (filename + ".partial")
    with wave.open(str(partial), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<h", 8192) * 320)
    info = dict(filename=filename, size_bytes=partial.stat().st_size,
                sha256=hashlib.sha256(partial.read_bytes()).hexdigest(),
                duration_seconds=.02, sample_rate=16000, channels=1)
    recording_id = archive.recording_journal.reserve("owner", **info)
    final = archive.audio_root / filename
    if promoted:
        partial.replace(final)
    return recording_id, partial, final, info


@pytest.mark.asyncio
async def test_sql_failure_after_real_rename_is_recovered_once_with_original_identity(stores):
    archive, _ = stores
    writer = SessionRecordingWriter(archive, session_id="owner", sample_rate=16000, reserve_bytes=0)
    await writer.start()
    writer.offer(struct.pack("<f", .25) * 320)
    with archive._connect() as conn:
        conn.execute("CREATE TRIGGER interrupt_registration BEFORE INSERT ON session_recordings "
                     "BEGIN SELECT RAISE(ABORT, 'simulated crash window'); END")
    with pytest.raises(sqlite3.IntegrityError, match="crash window"):
        await writer.stop()
    assert writer._completed.exists() and not writer._partial.exists()
    assert archive.recordings("owner") == []
    with archive._connect() as conn:
        reserved = dict(conn.execute("SELECT * FROM recording_journal").fetchone())
        conn.execute("DROP TRIGGER interrupt_registration")
    assert reserved["sha256"] == hashlib.sha256(writer._completed.read_bytes()).hexdigest()
    reopened = PostEventArchive(str(archive.database_path))
    reopened.initialize()
    assert await asyncio.to_thread(reopened.recording_journal.recover_batch, threading.Event()) == 1
    rows = reopened.recordings("owner")
    assert len(rows) == 1 and rows[0]["recording_id"] == reserved["recording_id"]
    assert rows[0]["sha256"] == reserved["sha256"] and rows[0]["duration_seconds"] == .02
    assert rows[0]["status"] == "completed"
    assert rows[0]["finalized_at"] == reserved["created_at"]
    with wave.open(str(reopened.recording_path("owner", rows[0]["recording_id"])), "rb") as audio:
        assert audio.getnframes() == 320 and audio.readframes(320) == struct.pack("<h", 8192) * 320
    assert reopened.recording_journal.recover_batch(threading.Event()) == 0
    assert reopened.recording_journal.promote(rows[0]["recording_id"]) == rows[0]


def test_old_recording_schema_migrates_without_inventing_finalization(stores):
    archive, history = stores
    filename = f"{uuid4().hex}.wav"
    path = archive.audio_root / filename
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 320)
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    created = "2020-01-02T03:04:05+00:00"
    with archive._connect() as conn:
        # Recreate the actual pre-migration table, preserving surrounding FK users.
        conn.execute("DROP TABLE session_recordings")
        conn.execute("""
            CREATE TABLE session_recordings (
                recording_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE, media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                duration_seconds REAL NOT NULL, sample_rate INTEGER NOT NULL,
                channels INTEGER NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            )
        """)
        conn.execute("INSERT INTO session_recordings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("legacy", "owner", filename, "audio/wav", len(original), digest,
                      .02, 16000, 1, created))
    archive.initialize()
    reopened = PostEventArchive(str(archive.database_path))
    reopened.initialize()  # The additive migration must also be idempotent.
    [recording] = reopened.recordings("owner")
    assert recording["status"] == "unknown" and recording["finalized_at"] is None
    assert recording["created_at"] == created and recording["sha256"] == digest
    assert reopened.recording_path("owner", "legacy") == path
    assert path.read_bytes() == original
    assert history.delete_session("owner")
    assert reopened.recordings("owner") == []


def test_reserved_partial_survives_stale_cleanup_and_recovers_without_guessing(stores):
    archive, _ = stores
    recording_id, partial, final, info = pending(archive, promoted=False)
    os.utime(partial, (1, 1))
    archive.initialize()
    assert partial.exists() and not final.exists()
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert not partial.exists() and final.exists()
    row = archive.recordings("owner")[0]
    assert row["recording_id"] == recording_id and row["sha256"] == info["sha256"]


@pytest.mark.parametrize("damage", ["hash", "size", "format", "short_fmt", "symlink", "traversal"])
def test_unverifiable_owned_files_and_journal_are_preserved_without_registration(stores, damage):
    archive, _ = stores
    recording_id, _, path, _ = pending(archive)
    outside = archive.audio_root.parent / "outside.wav"
    outside.write_bytes(path.read_bytes())
    if damage == "hash":
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
    elif damage == "size":
        with path.open("ab") as stream:
            stream.write(b"extra")
    elif damage in {"format", "short_fmt"}:
        data = (b"not WAV" if damage == "format" else
                b"RIFF" + (path.stat().st_size - 8).to_bytes(4, "little")
                + b"WAVEfmt \x01\0\0\0\0")
        data = data.ljust(path.stat().st_size, b"x")
        path.write_bytes(data)
        with archive._connect() as conn:
            conn.execute("UPDATE recording_journal SET sha256=?",
                         (hashlib.sha256(data).hexdigest(),))
    elif damage == "symlink":
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable")
    else:
        with archive._connect() as conn:
            conn.execute("UPDATE recording_journal SET filename='../outside.wav'")
    before = outside.read_bytes()
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert archive.recordings("owner") == []
    assert path.exists() and outside.read_bytes() == before
    with archive._connect() as conn:
        row = conn.execute("SELECT * FROM recording_journal WHERE recording_id=?",
                           (recording_id,)).fetchone()
        assert row["recovery_error"] in {"integrity_check_failed", "io_deferred"}
    assert archive.recording_journal.recover_batch(threading.Event()) == 0


def test_unknown_wavs_recent_partials_and_active_session_are_not_scanned(stores, monkeypatch):
    archive, history = stores
    recording_id, partial, path, _ = pending(archive, promoted=False)
    unknown = archive.audio_root / (uuid4().hex + ".wav")
    unknown.write_bytes(b"unknown asset must stay untouched")
    with archive._connect() as conn:
        conn.execute("UPDATE sessions SET ended_at=NULL WHERE session_id='owner'")
    monkeypatch.setattr(archive.recording_journal, "_verify",
                        lambda *_: pytest.fail("active/unknown scan"))
    archive.initialize()
    assert archive.recording_journal.recover_batch(threading.Event()) == 0
    assert partial.exists() and unknown.read_bytes() == b"unknown asset must stay untouched"
    assert not path.exists() and archive.recordings("owner") == []


def test_session_fk_delete_cleans_only_owned_final_and_partial_names(stores):
    archive, history = stores
    pending(archive)
    pending(archive, promoted=False)
    unknown = archive.audio_root / (uuid4().hex + ".wav")
    unknown.write_bytes(b"unknown")
    paths = archive.recording_paths_for_session("owner")
    assert paths == []
    assert history.delete_session("owner")
    assert archive.recording_journal.recover_batch(threading.Event()) == 2
    assert list(archive.audio_root.iterdir()) == [unknown]
    with archive._connect() as conn:
        assert conn.execute("SELECT count(*) FROM recording_journal").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_fk_delete_waiting_for_rename_transaction_cleans_future_final_name(
    stores, monkeypatch,
):
    archive, history = stores
    recording_id, partial, final, _ = pending(archive, promoted=False)
    entered, release = threading.Event(), threading.Event()
    original = Path.replace

    def delayed(path, target):
        if path == partial:
            entered.set()
            assert release.wait(3)
        return original(path, target)

    monkeypatch.setattr(Path, "replace", delayed)
    promotion = asyncio.create_task(asyncio.to_thread(
        archive.recording_journal.promote, recording_id,
    ))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        paths = await asyncio.to_thread(archive.recording_paths_for_session, "owner")
        assert paths == [] and not final.exists()
        deletion = asyncio.create_task(asyncio.to_thread(history.delete_session, "owner"))
        release.set()
        await promotion
        assert await deletion
        assert archive.recording_journal.recover_batch(threading.Event()) == 1
        assert not final.exists() and not partial.exists()
        assert archive.recordings("owner") == []
    finally:
        release.set()
        await promotion


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before_publish", "after_publish"])
async def test_cancelled_open_closes_late_real_fd_and_never_starts_consumer(
    stores, monkeypatch, boundary,
):
    archive, _ = stores
    writer = SessionRecordingWriter(archive, session_id="owner", sample_rate=16000, reserve_bytes=0)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    opened = []
    real_open = writer._open
    real_wave_open = wave.open

    def delayed_wave(stream, mode=None):
        if mode == "wb":
            opened.append(stream)
            entered.set()
            assert release.wait(3)
        return real_wave_open(stream, mode)

    def run_open():
        try:
            real_open()
            if boundary == "after_publish":
                opened.append(writer._stream)
                entered.set()
                assert release.wait(3)
        finally:
            finished.set()

    if boundary == "before_publish":
        monkeypatch.setattr(wave, "open", delayed_wave)
    monkeypatch.setattr(writer, "_open", run_open)
    task = asyncio.create_task(writer.start())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
    assert opened and all(stream.closed for stream in opened)
    assert writer._task is None and writer._stream is None
    assert not writer._partial.exists() and archive.recordings("owner") == []


@pytest.mark.asyncio
async def test_startup_has_one_bounded_worker_and_archive_refresh_advances_remaining(stores):
    archive, _ = stores
    for _ in range(35):
        pending(archive)
    runtime = CaptionRuntime(Settings(database_path=str(archive.database_path)))
    await runtime.initialize()
    first = runtime._recording_recovery_task
    runtime.request_recording_recovery()
    assert first is runtime._recording_recovery_task
    await first
    assert len(runtime.archive.recordings("owner")) == 32
    runtime.request_recording_recovery()
    await runtime._recording_recovery_task
    assert len(runtime.archive.recordings("owner")) == 35
    await runtime.shutdown()


def test_deletion_tombstone_preserves_unknown_replacement_instead_of_unlinking(stores):
    archive, history = stores
    recording_id, _, path, _ = pending(archive)
    archive.recording_journal._finish(recording_id)
    replacement = b"unknown".ljust(path.stat().st_size, b"x")
    path.write_bytes(replacement)
    assert archive.recording_paths_for_session("owner", legacy_only=True) == []
    assert history.delete_session("owner")
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert path.read_bytes() == replacement and archive.recordings("owner") == []
    with archive._connect() as conn:
        row = conn.execute("SELECT * FROM recording_journal").fetchone()
        assert row["session_id"] is None and row["owner_session_id"] == "owner"
        assert row["recovery_error"] == "integrity_check_failed"


def test_temporary_unlink_failure_keeps_owner_tombstone_for_verified_retry(stores, monkeypatch):
    archive, history = stores
    _, _, path, _ = pending(archive)
    assert history.delete_session("owner")
    original = Path.unlink
    blocked = True

    def unlink(candidate, *args, **kwargs):
        if candidate == path and blocked:
            raise PermissionError("simulated open handle")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    with archive._connect() as conn:
        row = conn.execute("SELECT * FROM recording_journal").fetchone()
        assert row["session_id"] is None and row["recovery_error"] == "io_deferred"
    assert path.exists()
    blocked = False
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert not path.exists()
    with archive._connect() as conn:
        assert conn.execute("SELECT count(*) FROM recording_journal").fetchone()[0] == 0


def test_deleted_owner_is_never_rebound_to_a_reused_session_id(stores):
    archive, history = stores
    recording_id, partial, final, _ = pending(archive, promoted=False)
    assert history.delete_session("owner")
    history.create_session(SessionState(
        session_id="owner", join_code="NEW", started_at=datetime.now(UTC),
    ), StartSessionRequest())
    assert archive.recording_journal.promote(recording_id) is None
    assert not partial.exists() and not final.exists()
    assert archive.recordings("owner") == []


def test_recovery_checks_file_size_and_regular_type_before_open(stores, monkeypatch):
    archive, _ = stores
    _, _, path, _ = pending(archive)
    with archive._connect() as conn:
        conn.execute("UPDATE recording_journal SET size_bytes=?", (MAX_MEDIA_BYTES + 1,))
    with path.open("r+b") as stream:
        stream.truncate(MAX_MEDIA_BYTES + 1)
    monkeypatch.setattr(os, "open", lambda *_: pytest.fail("oversized file opened"))
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert path.stat().st_size == MAX_MEDIA_BYTES + 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipe test")
def test_recovery_never_opens_a_fifo_disguised_as_owned_wav(stores, monkeypatch):
    archive, _ = stores
    _, _, path, _ = pending(archive)
    path.unlink()
    os.mkfifo(path)
    monkeypatch.setattr(os, "open", lambda *_: pytest.fail("FIFO must not be opened"))
    assert archive.recording_journal.recover_batch(threading.Event()) == 1


def test_hash_stop_fence_leaves_reservation_recoverable_without_late_registration(
    stores, monkeypatch,
):
    archive, _ = stores
    recording_id, _, path, info = pending(archive)
    # Keep the real WAV/header valid while extending the fixture to >1 hash chunk.
    frames = 1024 * 1024
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * frames)
    with archive._connect() as conn:
        conn.execute("UPDATE recording_journal SET size_bytes=?,sha256=?,duration_seconds=?",
                     (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest(),
                      frames / 16000))
    stop = threading.Event()
    original = hashlib.sha256
    received = []

    class StopAfterChunk:
        def __init__(self):
            self.digest = original()

        def update(self, block):
            received.append(len(block))
            self.digest.update(block)
            stop.set()

        def hexdigest(self):
            return self.digest.hexdigest()

    monkeypatch.setattr(hashlib, "sha256", StopAfterChunk)
    assert archive.recording_journal.recover_batch(stop) == 0
    assert received == [1024 * 1024] and archive.recordings("owner") == []
    with archive._connect() as conn:
        assert conn.execute("SELECT recovery_error FROM recording_journal").fetchone()[0] is None
    monkeypatch.setattr(hashlib, "sha256", original)
    assert archive.recording_journal.recover_batch(threading.Event()) == 1
    assert archive.recordings("owner")[0]["recording_id"] == recording_id


@pytest.mark.asyncio
async def test_boot_does_not_wait_for_hash_and_shutdown_fences_worker(stores, monkeypatch):
    archive, _ = stores
    pending(archive)
    runtime = CaptionRuntime(Settings(database_path=str(archive.database_path)))
    entered = threading.Event()

    def wait_for_shutdown(row, path, stop):
        entered.set()
        assert stop.wait(3)
        raise RecoveryStopped

    monkeypatch.setattr(runtime.archive.recording_journal, "_verify", wait_for_shutdown)
    await asyncio.wait_for(runtime.initialize(), 1)
    assert await asyncio.to_thread(entered.wait, 1)
    await asyncio.wait_for(runtime.shutdown(), 1)
    assert runtime._recording_recovery_task.done() and archive.recordings("owner") == []
    with archive._connect() as conn:
        assert conn.execute("SELECT recovery_error FROM recording_journal").fetchone()[0] is None


@pytest.mark.asyncio
async def test_cancelled_recovery_retains_worker_slot_and_old_stop_event(stores, monkeypatch):
    archive, _ = stores
    pending(archive)
    runtime = CaptionRuntime(Settings(database_path=str(archive.database_path)))
    entered, release = threading.Event(), threading.Event()
    original = runtime.archive.recording_journal._verify

    def delayed(row, path, stop):
        entered.set()
        assert release.wait(3)
        return original(row, path, stop)

    monkeypatch.setattr(runtime.archive.recording_journal, "_verify", delayed)
    await runtime.initialize()
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task, batch, stop = (runtime._recording_recovery_task, runtime._recording_recovery_batch,
                             runtime._recording_recovery_stop)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        runtime.request_recording_recovery()
        assert runtime._recording_recovery_task is task and stop.is_set() and not batch.done()
        release.set()
        await batch
        assert archive.recordings("owner") == []
        runtime.request_recording_recovery()
        assert runtime._recording_recovery_stop is not stop and stop.is_set()
        await runtime._recording_recovery_task
        assert len(archive.recordings("owner")) == 1
    finally:
        release.set()
        await runtime.shutdown()
