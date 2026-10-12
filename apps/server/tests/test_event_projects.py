"""Projects group events without owning/destroying historical transcript sessions."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from langtextflow.models import (
    CaptionStage, SessionState, StartSessionRequest, TranscriptEvent,
)
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.session_repository import SessionRepository


def _history(tmp_path):
    history = SessionRepository(str(tmp_path / "app.db"))
    history.initialize()
    return history, ProjectRepository(str(tmp_path / "app.db"))


def _session(
    history: SessionRepository, session_id: str,
    project_id: str | None = None,
) -> None:
    request = StartSessionRequest(
        project_id=project_id,
        source_language="ko", target_languages=["en"], engine="mock",
    )
    state = SessionState(
        session_id=session_id, join_code="ABC123",
        started_at=datetime.now(UTC), engine="mock",
    )
    history.create_session(state, request)
    history.upsert_segment(
        session_id, TranscriptEvent(
            segment_id="original", version=1, stage=CaptionStage.COMMITTED,
            source_language="ko", text="행사 내용", committed=True, start_ms=0,
        ),
    )
    history.mark_ended(session_id)


def test_project_crud_and_assignment_preserve_transcripts_on_delete(tmp_path) -> None:
    history, repository = _history(tmp_path)
    first = repository.create(ProjectInput(
        title="  국제 컨퍼런스 ", event_date="2026-10-12", location="서울",
    ))
    second = repository.create(ProjectInput(title="두 번째 행사"))
    assert first.title == "국제 컨퍼런스"
    assert first.event_date.isoformat() == "2026-10-12"

    _session(history, "prior-session")
    _session(history, "new-session", first.project_id)
    assert history.get_session("prior-session").project_id is None
    assert history.get_session("new-session").project_id == first.project_id
    assert repository.get(first.project_id).session_count == 1

    repository.assign("prior-session", first.project_id)
    assert repository.get(first.project_id).session_count == 2
    repository.assign("prior-session", second.project_id)
    assert repository.get(first.project_id).session_count == 1
    assert repository.get(second.project_id).session_count == 1
    repository.assign("prior-session", None)
    assert history.get_session("prior-session").project_id is None

    updated = repository.update(
        first.project_id,
        ProjectInput(title="행사 정리", status="completed", description="후속 검토"),
    )
    assert updated.title == "행사 정리"
    assert updated.status == "completed"
    assert repository.delete(first.project_id)
    assert history.get_session("new-session").project_id is None
    assert history.segments("new-session")[0].text == "행사 내용"
    assert repository.get(first.project_id) is None
    assert repository.delete(first.project_id) is False


def test_unknown_project_rolls_back_new_session_insertion(tmp_path) -> None:
    history, repository = _history(tmp_path)
    with pytest.raises(ValueError, match="project not found"):
        _session(history, "invalid-session", "nonexistent-project")
    assert history.get_session("invalid-session") is None
    assert history.list_sessions() == []
    with pytest.raises(KeyError, match="session not found"):
        repository.assign("absent", "nonexistent")
    _session(history, "valid-session")
    with pytest.raises(KeyError, match="project not found"):
        repository.assign("valid-session", "nonexistent")
    with pytest.raises(KeyError, match="session not found"):
        repository.assign("absent", None)


def test_existing_sessions_migrate_without_project_assignment(tmp_path) -> None:
    db = Path(tmp_path / "existing.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, join_code TEXT NOT NULL,
                title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                presenter TEXT, preset TEXT NOT NULL,
                source_language TEXT NOT NULL, target_languages_json TEXT NOT NULL,
                engine TEXT NOT NULL, correction_provider TEXT NOT NULL DEFAULT 'none',
                correction_model TEXT, translation_provider TEXT NOT NULL,
                translation_model TEXT, context_json TEXT NOT NULL,
                started_at TEXT NOT NULL, ended_at TEXT, interrupted INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    history = SessionRepository(str(db))
    history.initialize()
    project_repo = ProjectRepository(str(db))
    assert project_repo.list() == []
    _session(history, "existing-db-session")
    assert history.get_session("existing-db-session").project_id is None
    assert history.list_sessions()[0].project_id is None
    history.initialize()  # idempotent migration
    assert history.get_session("existing-db-session").segment_count == 1


@pytest.mark.parametrize("bad", [" ", "", "  \n "])
def test_invalid_project_title_rejected(bad: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProjectInput(title=bad)


@pytest.mark.asyncio
async def test_project_api_crud_and_operator_boundary(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException
    from langtextflow import main

    history, repository = _history(tmp_path)
    monkeypatch.setattr(main, "project_repository", repository)
    monkeypatch.setattr(
        main, "runtime",
        SimpleNamespace(history=history, state=SessionState(running=False)),
    )
    local = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"), headers={},
    )
    remote = SimpleNamespace(
        client=SimpleNamespace(host="192.168.1.50"), headers={},
    )
    created = await main.create_project(local, ProjectInput(title="API event"))
    assert created.session_count == 0
    assert (await main.projects(local))[0].project_id == created.project_id
    _session(history, "api-session")
    detail = await main.assign_session_project(
        local, "api-session",
        main.ProjectAssignment(project_id=created.project_id),
    )
    assert detail.project_id == created.project_id
    with pytest.raises(HTTPException) as error:
        await main.projects(remote)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await main.assign_session_project(
            local, "api-session",
            main.ProjectAssignment(project_id="no-project"),
        )
    assert error.value.status_code == 404
    await main.delete_project(local, created.project_id)
    assert history.get_session("api-session").project_id is None
    assert history.segments("api-session")[0].text == "행사 내용"


@pytest.mark.asyncio
async def test_invalid_project_does_not_interrupt_live_session(tmp_path) -> None:
    from langtextflow.config import Settings
    from langtextflow.runtime import CaptionRuntime

    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    await runtime.initialize()
    live = await runtime.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        engine="mock", translation_provider="none",
    ))
    assert live.running is True
    session_id = live.session_id
    try:
        with pytest.raises(ValueError, match="project not found"):
            await runtime.start(StartSessionRequest(
                project_id="missing",
                source_language="ko", target_languages=["en"],
                engine="mock", translation_provider="none",
            ))
        assert runtime.state.running is True
        assert runtime.state.session_id == session_id
    finally:
        await runtime.stop()
        await runtime.shutdown()
