"""API-level citation fail-closed and source revision checks for #80."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langtextflow import post_event_api
from langtextflow.config import Settings
from langtextflow.models import (
    CaptionStage,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.post_event_api import install_post_event_routes
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.session_repository import SessionRepository


def _app(tmp_path):
    db = str(tmp_path / "events.db")
    history = SessionRepository(db)
    history.initialize()
    history.create_session(
        SessionState(
            session_id="source-1", join_code="ABC123",
            started_at=datetime.now(UTC), engine="mock",
        ),
        StartSessionRequest(
            source_language="ko", target_languages=["en"], engine="mock",
        ),
    )
    history.upsert_segment("source-1", TranscriptEvent(
        segment_id="caption-1", version=1, stage=CaptionStage.COMMITTED,
        committed=True, source_language="ko",
        text="하나님이 세상을 사랑하십니다.", start_ms=1000, end_ms=4000,
    ))
    history.mark_ended("source-1")
    archive = PostEventArchive(db)
    archive.initialize()
    @asynccontextmanager
    async def lifespan(_):
        yield
        await shutdown()

    app = FastAPI(lifespan=lifespan)
    runtime = SimpleNamespace(
        history=history, state=SessionState(session_id=None, running=False),
    )
    shutdown = install_post_event_routes(
        app, runtime, archive,
        Settings(database_path=db), lambda request: None,
    )
    return app, archive, history


def _settle(client: TestClient, archive: PostEventArchive):
    for _ in range(200):
        summary = archive.latest_summary("source-1")
        if summary is not None and summary["status"] != "running":
            return summary
        time.sleep(0.01)
    pytest.fail("summary worker did not settle")


def test_uncited_model_draft_is_recorded_as_error_not_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    app, archive, _ = _app(tmp_path)

    async def generated(*args, **kwargs):
        return "This summary invents claims and provides no source citations."

    monkeypatch.setattr(post_event_api, "generate_summary", generated)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/history/source-1/summary",
            json={"provider": "ollama", "model": "mock-local", "language": "ko"},
        )
        assert response.status_code == 202
        entry = _settle(client, archive)
        assert entry["status"] == "error"
        assert not entry["content"]
        assert "Summary model unavailable" in entry["error"]


def test_valid_cited_draft_can_be_saved_but_becomes_stale_on_source_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    app, archive, history = _app(tmp_path)

    async def generated(*args, **kwargs):
        return "사랑에 관해 말씀하셨습니다. [caption-1 at 1s]"

    monkeypatch.setattr(post_event_api, "generate_summary", generated)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/history/source-1/summary",
            json={"provider": "ollama", "model": "mock-local", "language": "ko"},
        )
        assert response.status_code == 202
        entry = _settle(client, archive)
        assert entry["status"] == "completed"
        assert "[caption-1 at 1s]" in entry["content"]
        assert client.get("/api/v1/history/source-1/archive").json()["summary_stale"] is False

        history.upsert_segment("source-1", TranscriptEvent(
            segment_id="caption-1", version=2, stage=CaptionStage.COMMITTED,
            committed=True, source_language="ko",
            text="변경된 원문이 저장되었습니다.", start_ms=1000, end_ms=4000,
        ))
        archive_view = client.get("/api/v1/history/source-1/archive")
        assert archive_view.status_code == 200
        assert archive_view.json()["summary_stale"] is True


def test_review_preserves_model_draft_and_locks_approved_version(monkeypatch, tmp_path):
    app, archive, history = _app(tmp_path)
    original = "사랑에 관해 말씀하셨습니다. [caption-1 at 1s]"

    async def generated(*args, **kwargs):
        return original

    monkeypatch.setattr(post_event_api, "generate_summary", generated)
    with TestClient(app) as client:
        root = "/api/v1/history/source-1"
        client.post(root + "/summary", json={"model": "mock-local"})
        summary = _settle(client, archive)
        path = root + f"/summaries/{summary['summary_id']}/reviews"
        assert client.post(path, json={"content": "No citation"}).status_code == 422
        assert client.post(path, json={"content": "Wrong [caption-1 at 2s]"}).status_code == 422
        assert client.post(path.replace("source-1", "other"), json={
            "content": original,
        }).status_code == 404

        revised = "하나님의 사랑을 설명했습니다. [caption-1 at 1s]"
        draft = client.post(path, json={"content": revised})
        assert draft.status_code == 201
        draft_id = draft.json()["review_id"]
        # Another tab with an old revision cannot silently overwrite an edit.
        assert client.post(path, json={"content": original}).status_code == 409
        approval = client.post(path, json={
            "content": revised, "approved": True, "expected_review_id": draft_id,
        })
        assert approval.status_code == 201
        approved_id = approval.json()["review_id"]
        assert client.post(path, json={
            "content": original, "expected_review_id": approved_id,
        }).status_code == 409

        view = client.get(root + "/archive").json()
        assert view["summary"]["content"] == original
        assert view["summary"]["review"]["content"] == revised
        assert view["summary"]["review"]["approved"]
        reopened = PostEventArchive(archive.database_path)
        reopened.initialize()
        assert reopened.latest_summary("source-1")["review"]["review_id"] == approved_id
        # Idempotent generation preserves the existing approval.
        assert client.post(root + "/summary", json={"model": "mock-local"}).json()[
            "review"
        ]["review_id"] == approved_id

        history.upsert_segment("source-1", TranscriptEvent(
            segment_id="caption-1", version=2, stage=CaptionStage.COMMITTED,
            committed=True, source_language="ko", text="변경된 원문",
            start_ms=1000, end_ms=4000,
        ))
        assert client.get(root + "/archive").json()["summary_stale"]
        assert client.post(path, json={"content": original}).status_code == 409
        assert history.delete_session("source-1")
        assert archive.latest_review(summary["summary_id"]) is None


def test_only_completed_summaries_can_be_reviewed(tmp_path):
    _, archive, _ = _app(tmp_path)
    record = archive.begin_summary(
        "source-1", language="ko", provider="ollama", model="mock-local",
        transcript_sha256="a" * 64, prompt_version="test",
    )
    with pytest.raises(ValueError, match="completed"):
        archive.review_summary(
            "source-1", record["summary_id"], content="test",
            approved=True, expected_review_id=None,
        )


def test_total_summary_deadline_records_failure(monkeypatch, tmp_path):
    app, archive, _ = _app(tmp_path)

    async def stalled(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(post_event_api, "generate_summary", stalled)
    monkeypatch.setattr(post_event_api, "SUMMARY_JOB_TIMEOUT_SECONDS", 0.01)
    with TestClient(app) as client:
        assert client.post("/api/v1/history/source-1/summary", json={}).status_code == 202
        assert _settle(client, archive)["status"] == "error"


def test_summary_admission_is_bounded_and_shutdown_marks_jobs_interrupted(monkeypatch, tmp_path):
    app, archive, history = _app(tmp_path)
    for sid in ("source-2", "source-3"):
        history.create_session(
            SessionState(session_id=sid, join_code="ABC123", engine="mock",
                         started_at=datetime.now(UTC)),
            StartSessionRequest(engine="mock"),
        )
        history.upsert_segment(sid, TranscriptEvent(
            segment_id="caption-1", version=1, stage=CaptionStage.COMMITTED,
            committed=True, source_language="ko", text="원문", start_ms=0,
        ))
        history.mark_ended(sid)

    async def stalled(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(post_event_api, "generate_summary", stalled)
    with TestClient(app) as client:
        for sid in ("source-1", "source-2"):
            assert client.post(f"/api/v1/history/{sid}/summary", json={}).status_code == 202
        assert client.post("/api/v1/history/source-3/summary", json={}).status_code == 429
        assert archive.latest_summary("source-3") is None
    for sid in ("source-1", "source-2"):
        summary = archive.latest_summary(sid)
        assert summary["status"] == "error"
        assert "shutdown" in summary["error"]
