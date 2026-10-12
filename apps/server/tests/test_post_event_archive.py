"""Post-event archive persistence, input boundaries, and summary policy regressions."""
from __future__ import annotations

import io
import wave
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langtextflow.config import Settings
from langtextflow.models import (
    CaptionStage,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.post_event_api import install_post_event_routes
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_summary import (
    chunks,
    generate_summary,
    transcript_source,
)
from langtextflow.session_repository import SessionRepository


def _seed(tmp_path):
    db = str(tmp_path / "events.db")
    history = SessionRepository(db)
    history.initialize()
    request = StartSessionRequest(
        source_language="ko", target_languages=["en"], engine="mock",
    )
    history.create_session(
        SessionState(
            session_id="s-1", join_code="ABCDE1", started_at=datetime.now(UTC),
            engine="mock",
        ),
        request,
    )
    history.mark_ended("s-1")
    archive = PostEventArchive(db)
    archive.initialize()
    return history, archive


def _segment(*, committed=True):
    return TranscriptEvent(
        segment_id="caption-1", version=2,
        stage=CaptionStage.COMMITTED if committed else CaptionStage.STABLE,
        committed=committed, source_language="ko",
        text="행사의 주요 계획과 후속 일정을 논의했습니다.",
        translations={"en": "Discussed the event."},
        start_ms=1000, end_ms=5000,
    )


def _wav(data: bytes = b"\0" * 32000) -> bytes:
    result = io.BytesIO()
    with wave.open(result, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(data)
    return result.getvalue()


def test_archive_records_survive_reopen_and_cascade_with_session(tmp_path) -> None:
    history, archive = _seed(tmp_path)
    path = archive.audio_root / "test.wav"
    path.write_bytes(_wav())
    info = archive.insert_recording(
        "s-1", filename="test.wav", size_bytes=path.stat().st_size,
        sha256="a" * 64, duration_seconds=1.0, sample_rate=16000, channels=1,
    )
    assert archive.recording_path("s-1", info["recording_id"]) == path
    assert len(PostEventArchive(str(tmp_path / "events.db")).recordings("s-1")) == 1
    first = archive.begin_summary(
        "s-1", language="ko", provider="ollama", model="test",
        transcript_sha256="b" * 64, prompt_version="post-event-v1",
    )
    archive.finish_summary(first["summary_id"], content="검증된 요약")
    assert archive.latest_summary("s-1")["content"] == "검증된 요약"
    assert history.delete_session("s-1")
    assert archive.recordings("s-1") == []
    assert archive.latest_summary("s-1") is None


def test_pending_summary_recovered_on_restart(tmp_path) -> None:
    _, archive = _seed(tmp_path)
    record = archive.begin_summary(
        "s-1", language="ko", provider="ollama", model="test",
        transcript_sha256="c" * 64, prompt_version="post-event-v1",
    )
    archive.initialize()
    assert archive.summary_by_id(record["summary_id"])["status"] == "error"


def test_transcript_uses_only_committed_source_and_hash_is_deterministic() -> None:
    source, digest = transcript_source([_segment(), _segment(committed=False)])
    assert "주요 계획" in source
    assert "Discussed the event" not in source
    assert source.count("caption-1") == 1
    assert transcript_source([_segment()])[1] == digest
    with pytest.raises(ValueError, match="no committed"):
        transcript_source([_segment(committed=False)])


def test_chunks_are_bounded() -> None:
    source = "abcdef " * 10000
    parts = chunks(source)
    assert all(0 < len(part) <= 6000 for part in parts)
    assert "".join(parts) == source
    with pytest.raises(ValueError, match="chunk limit"):
        chunks("source", limit=32)


@pytest.mark.asyncio
async def test_ollama_summary_is_real_provider_call_and_untrusted_guard(monkeypatch) -> None:
    from langtextflow import post_event_summary

    requests = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return httpx.Response(
                200,
                json={"message": {"content": "근거에 따른 행사 요약"}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(post_event_summary.httpx, "AsyncClient", FakeClient)
    result = await generate_summary(
        "[caption-1 at 0s] 안전한 원문",
        language="ko", provider="ollama", model="test",
        settings=Settings(), allow_external_provider=False,
    )
    assert "행사 요약" in result
    assert "/api/chat" in requests[0][0]
    assert "untrusted data" in requests[0][1]["json"]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_summary_provider_must_be_explicit() -> None:
    with pytest.raises(ValueError, match="explicit opt-in"):
        await generate_summary(
            "sample", language="ko", provider="openai-compatible",
            model="external", settings=Settings(), allow_external_provider=False,
        )


def test_archive_http_wav_upload_and_security(tmp_path) -> None:
    from types import SimpleNamespace

    history, archive = _seed(tmp_path)
    app = FastAPI()
    runtime = SimpleNamespace(
        history=history,
        state=SessionState(session_id=None, running=False),
    )
    install_post_event_routes(
        app, runtime, archive,
        Settings(database_path=str(tmp_path / "events.db"), storage_reserve_mb=64),
        lambda request: None,
    )

    with TestClient(app) as client:
        audio = _wav()
        response = client.post(
            "/api/v1/history/s-1/recordings",
            data=audio, headers={"content-type": "audio/wav"},
        )
        assert response.status_code == 201, response.text
        recording_id = response.json()["recording_id"]
        assert response.json()["duration_seconds"] == 1.0
        downloaded = client.get(
            f"/api/v1/history/s-1/recordings/{recording_id}/audio",
        )
        assert downloaded.status_code == 200
        assert downloaded.content == audio
        rejected = client.post(
            "/api/v1/history/s-1/recordings",
            data=b"not a wav", headers={"content-type": "audio/wav"},
        )
        assert rejected.status_code == 422
        assert len(archive.recordings("s-1")) == 1
        missing = client.get("/api/v1/history/s-1/recordings/unknown/audio")
        assert missing.status_code == 404
        active = runtime.state
        active.session_id = "s-1"
        active.running = True
        assert client.get("/api/v1/history/s-1/archive").status_code == 409


def test_recording_path_rejects_symlink_even_to_managed_audio(tmp_path) -> None:
    """A symlink may point INSIDE the audio root and must still be rejected."""
    history, archive = _seed(tmp_path)
    owned = archive.audio_root / "original.wav"
    owned.write_bytes(_wav())
    link = archive.audio_root / "link.wav"
    try:
        link.symlink_to(owned)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")
    record = archive.insert_recording(
        "s-1", filename="link.wav", size_bytes=owned.stat().st_size,
        sha256="d" * 64, duration_seconds=1.0, sample_rate=16000, channels=1,
    )
    assert archive.recording_path("s-1", record["recording_id"]) is None
    assert history.get_session("s-1") is not None


def test_recording_path_is_session_scoped(tmp_path) -> None:
    history, archive = _seed(tmp_path)
    raw_path = archive.audio_root / "owned.wav"
    raw_path.write_bytes(_wav())
    record = archive.insert_recording(
        "s-1", filename="owned.wav", size_bytes=raw_path.stat().st_size,
        sha256="e" * 64, duration_seconds=1.0, sample_rate=16000, channels=1,
    )
    assert archive.recording_path("not-this-session", record["recording_id"]) is None
    assert archive.recording_path("s-1", record["recording_id"]) == raw_path
