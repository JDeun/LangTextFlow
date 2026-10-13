"""API-level citation fail-closed and source revision checks for #80."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langtextflow import post_event_api
from langtextflow.config import Settings
from langtextflow.models import CaptionStage, SessionState, StartSessionRequest, TranscriptEvent
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
    app = FastAPI()
    runtime = SimpleNamespace(
        history=history, state=SessionState(session_id=None, running=False),
    )
    install_post_event_routes(
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
