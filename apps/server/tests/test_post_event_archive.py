"""Post-event archive persistence, input boundaries, and summary policy regressions."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
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
    source_chunks,
    transcript_source,
)
from langtextflow.session_repository import SessionRepository
from langtextflow.summary_evidence import allowed_source_citations, validate_summary_citations


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


def test_source_fragments_keep_original_markers_and_bounded_overlap() -> None:
    segment = _segment().model_copy(update={"text": "ABCDE" * 3500, "speaker": "ASR A"})
    source, _ = transcript_source([segment])
    parts = source_chunks(source)
    texts = []
    for part in parts:
        assert 0 < len(part) <= 6000
        assert allowed_source_citations(part) == {"[caption-1 at 1s]"}
        body = part.partition("] ")[2]
        metadata, end = json.JSONDecoder().raw_decode(body)
        assert metadata == {"speaker": "ASR A"}
        texts.append(body[end + 1:])
    assert len(parts) > 2
    assert all(a[-400:] == b[:400] for a, b in zip(texts, texts[1:], strict=False))
    assert texts[0] + "".join(text[400:] for text in texts[1:]) == segment.text


def test_source_chunk_limits_and_empty_evidence_remain_bounded(monkeypatch) -> None:
    from langtextflow import post_event_summary

    source = "[source at 1s] {} " + "A" * 239_970
    assert len(source_chunks(source)) <= 48
    with pytest.raises(ValueError, match="oversized"):
        source_chunks(source + "A" * 100)
    with pytest.raises(ValueError, match="no spoken text"):
        source_chunks("[source at 1s] {} ")
    monkeypatch.setattr(post_event_summary, "MAX_CHUNKS", 2)
    with pytest.raises(ValueError, match="maximum.*chunk"):
        source_chunks(source)


def test_source_format_upgrade_marks_old_summary_stale_without_rewriting_approval(tmp_path):
    _, archive = _seed(tmp_path)
    segment = _segment()
    legacy = f"[caption-1 at 1s] {segment.source_text}"
    record = archive.begin_summary(
        "s-1", language="ko", provider="ollama", model="test",
        transcript_sha256=hashlib.sha256(legacy.encode()).hexdigest(),
        prompt_version="post-event-v2-cited",
    )
    original = "Original model draft [caption-1 at 1s]"
    reviewed = "Approved operator text [caption-1 at 1s]"
    archive.finish_summary(record["summary_id"], content=original)
    archive.review_summary(
        "s-1", record["summary_id"], content=reviewed, approved=True, expected_review_id=None,
    )
    assert archive.transcripts.summary_stale(archive.latest_summary("s-1"), [segment])
    assert archive.summary_by_id(record["summary_id"])["content"] == original
    assert archive.latest_review(record["summary_id"])["content"] == reviewed
    assert archive.latest_review(record["summary_id"])["approved"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
async def test_long_summary_uses_real_http_path_with_source_overlap_and_cited_reduction(
    monkeypatch, provider,
):
    from langtextflow import post_event_summary

    source, _ = transcript_source([
        _segment().model_copy(update={
            "segment_id": f"segment-{index}", "start_ms": index * 1000,
            "text": f"Original source {index}. " * 850, "speaker": f"ASR {index}",
        }) for index in range(4)
    ])
    mapped = source_chunks(source)
    seen = []
    reductions = []
    original_chunks = post_event_summary.chunks

    def note_chunks(text):
        parts = original_chunks(text)
        assert "\n".join(parts) == text  # Notes do not gain source-context overlap.
        reductions.append(parts)
        return parts

    def respond(request):
        payload = json.loads(request.content)
        task = json.loads(payload["messages"][1]["content"])
        evidence = task["transcript_evidence"]
        assert len(evidence) <= 6000
        seen.append(task)
        markers = list(dict.fromkeys(re.findall(r"\[segment-\d+ at \d+s\]", evidence)))
        assert markers
        if task["task"] == "final_event_summary":
            assert set(markers) == allowed_source_citations(source)
            content = "Cited draft: " + " ".join(markers)
        else:
            content = " ".join(markers) + " evidence notes" * 140
        response = (
            {"message": {"content": content}} if provider == "ollama"
            else {"choices": [{"message": {"content": content}}]}
        )
        return httpx.Response(200, json=response)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(post_event_summary.httpx, "AsyncClient", lambda **options: client_class(
        transport=httpx.MockTransport(respond), **options,
    ))
    monkeypatch.setattr(post_event_summary, "chunks", note_chunks)
    result = await generate_summary(
        source, language="ko", provider=provider, model="mock-http",
        settings=Settings(), allow_external_provider=True,
    )
    assert [item["transcript_evidence"] for item in seen[:len(mapped)]] == mapped
    assert all(item["evidence_kind"] == "source_transcript" for item in seen[:len(mapped)])
    assert all(item["evidence_kind"] == "intermediate_notes" for item in seen[len(mapped):])
    assert all(
        allowed_source_citations(part) <= allowed_source_citations(source) for part in mapped
    )
    assert len(reductions) >= 2
    assert len(validate_summary_citations(source, result)) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
@pytest.mark.parametrize("failure", ["timeout", "empty"])
async def test_long_summary_http_failure_never_produces_partial_final(
    monkeypatch, provider, failure,
):
    from langtextflow import post_event_summary

    source, _ = transcript_source([_segment().model_copy(update={"text": "A" * 14_000})])
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private provider detail", request=request)
        return httpx.Response(200, json={})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(post_event_summary.httpx, "AsyncClient", lambda **options: client_class(
        transport=httpx.MockTransport(respond), **options,
    ))
    expected = httpx.TimeoutException if failure == "timeout" else ValueError
    with pytest.raises(expected):
        await generate_summary(
            source, language="ko", provider=provider, model="mock-http",
            settings=Settings(), allow_external_provider=True,
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ollama_summary_is_real_provider_call_and_untrusted_guard(monkeypatch) -> None:
    from langtextflow import post_event_summary

    requests = []

    native_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": "근거에 따른 행사 요약"}})

    monkeypatch.setattr(post_event_summary.httpx, "AsyncClient", lambda **options:
        native_client(transport=httpx.MockTransport(respond), **options))
    result = await generate_summary(
        "[caption-1 at 0s] {} 안전한 원문",
        language="ko", provider="ollama", model="test",
        settings=Settings(), allow_external_provider=False,
    )
    assert "행사 요약" in result
    assert "/api/chat" in str(requests[0].url)
    assert "untrusted data" in json.loads(requests[0].content)["messages"][0]["content"]


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
        assert response.json()["status"] == "completed"
        assert datetime.fromisoformat(response.json()["finalized_at"]).tzinfo is not None
        [saved] = client.get("/api/v1/history/s-1/archive").json()["recordings"]
        assert saved == response.json()
        downloaded = client.get(
            f"/api/v1/history/s-1/recordings/{recording_id}/audio",
        )
        assert downloaded.status_code == 200
        assert downloaded.content == audio
        assert downloaded.headers["content-disposition"].startswith("inline;")
        attachment = client.get(
            f"/api/v1/history/s-1/recordings/{recording_id}/audio?download=true",
        )
        assert attachment.content == audio
        assert attachment.headers["content-disposition"].startswith("attachment;")
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



def test_running_summary_is_single_flight_and_retryable(tmp_path) -> None:
    """An atomic DB constraint prevents duplicate LLM work under concurrent POST."""
    _, archive = _seed(tmp_path)
    params = {
        "language": "ko",
        "provider": "ollama",
        "model": "test",
        "transcript_sha256": "f" * 64,
        "prompt_version": "post-event-v1",
    }
    initial = archive.begin_summary("s-1", **params)
    assert initial["status"] == "running"
    with pytest.raises(
        sqlite3.IntegrityError,
        match="UNIQUE constraint failed: session_summaries.session_id",
    ):
        archive.begin_summary("s-1", **params)
    archive.finish_summary(initial["summary_id"], content="검토 전 초안")
    retried = archive.begin_summary("s-1", **params)
    assert retried["summary_id"] != initial["summary_id"]
    archive.finish_summary(retried["summary_id"], error="timeout")
    assert archive.latest_summary("s-1")["status"] == "error"


def test_running_summary_single_flight_survives_restart_migration(tmp_path) -> None:
    _, archive = _seed(tmp_path)
    row = archive.begin_summary(
        "s-1", language="ko", provider="ollama", model="test",
        transcript_sha256="f" * 64, prompt_version="post-event-v1",
    )
    # Restart must mark abandoned work failed before re-establishing the index.
    archive.initialize()
    assert archive.summary_by_id(row["summary_id"])["status"] == "error"
    next_row = archive.begin_summary(
        "s-1", language="ko", provider="ollama", model="test",
        transcript_sha256="f" * 64, prompt_version="post-event-v1",
    )
    assert next_row["status"] == "running"
