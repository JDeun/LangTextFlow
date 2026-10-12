"""Event-wide archive index: scoped provenance, pagination, stale detection."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from langtextflow.models import (
    CaptionStage, SessionState, StartSessionRequest, TranscriptEvent,
)
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_summary import transcript_source
from langtextflow.project_archive import build_project_archive_manifest
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.session_repository import SessionRepository


def _seed(tmp_path):
    db = str(tmp_path / "manifest.db")
    history = SessionRepository(db)
    history.initialize()
    projects = ProjectRepository(db)
    archive = PostEventArchive(db)
    archive.initialize()
    return history, projects, archive


def _session(history, project_id: str, identifier: str, text: str) -> None:
    history.create_session(
        SessionState(
            session_id=identifier, join_code="TEST12", engine="mock",
            started_at=datetime.now(UTC),
        ),
        StartSessionRequest(
            project_id=project_id, engine="mock",
            source_language="ko", target_languages=["en"],
        ),
    )
    history.upsert_segment(
        identifier,
        TranscriptEvent(
            segment_id="speech", version=1, stage=CaptionStage.COMMITTED,
            source_language="ko", text=text, committed=True, start_ms=0,
        ),
    )
    history.mark_ended(identifier)


def test_manifest_has_paged_project_scoped_recording_and_summary_references(tmp_path) -> None:
    history, projects, archive = _seed(tmp_path)
    project = projects.create(ProjectInput(title="Annual keynote"))
    other = projects.create(ProjectInput(title="Private planning"))
    _session(history, project.project_id, "event-1", "첫번째 강연 내용")
    _session(history, project.project_id, "event-2", "두번째 강연 내용")
    _session(history, other.project_id, "private", "비공개 강연")
    source, source_hash = transcript_source(history.segments("event-1"))
    assert "첫번째" in source
    summary = archive.begin_summary(
        "event-1", language="ko", provider="ollama", model="local",
        transcript_sha256=source_hash, prompt_version="post-event-v1",
    )
    archive.finish_summary(summary["summary_id"], content="실제 강연에서 나온 사항")
    audio_path = archive.audio_root / "audio.wav"
    audio_path.write_bytes(b"test")
    recording = archive.insert_recording(
        "event-1", filename="audio.wav", size_bytes=4,
        sha256="b" * 64, duration_seconds=2, sample_rate=16000, channels=1,
    )
    first = build_project_archive_manifest(
        projects, history, archive, project.project_id, limit=1, offset=0,
    )
    next_page = build_project_archive_manifest(
        projects, history, archive, project.project_id, limit=1, offset=1,
    )
    assert first["pagination"]["has_more"]
    assert next_page["pagination"]["has_more"] is False
    assert first["pagination"]["total_sessions_at_query"] == 2
    combined = first["sessions"] + next_page["sessions"]
    assert {item["session_id"] for item in combined} == {"event-1", "event-2"}
    assert all(item["session_id"] != "private" for item in combined)
    recorded = next(item for item in combined if item["session_id"] == "event-1")
    assert recorded["summary"]["summary_id"] == summary["summary_id"]
    assert recorded["summary"]["stale"] is False
    assert recorded["recordings"][0]["recording_id"] == recording["recording_id"]
    assert recorded["recordings"][0]["sha256"] == "b" * 64
    assert recorded["recording_duration_seconds"] == 2
    serialized = json.dumps(first, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "비공개 강연" not in serialized
    assert "recordings/audio.wav" not in serialized


def test_manifest_marks_changed_transcripts_stale_and_reassignment_excludes_them(
    tmp_path,
) -> None:
    history, projects, archive = _seed(tmp_path)
    project = projects.create(ProjectInput(title="Conference"))
    _session(history, project.project_id, "event-1", "원본 전사")
    _, original_hash = transcript_source(history.segments("event-1"))
    draft = archive.begin_summary(
        "event-1", language="ko", provider="ollama", model="local",
        transcript_sha256=original_hash, prompt_version="post-event-v1",
    )
    archive.finish_summary(draft["summary_id"], content="원본 기반 요약")
    history.upsert_segment(
        "event-1",
        TranscriptEvent(
            segment_id="speech", version=2, stage=CaptionStage.COMMITTED,
            committed=True, source_language="ko",
            text="수정된 확정 전사", start_ms=0,
        ),
    )
    manifest = build_project_archive_manifest(
        projects, history, archive, project.project_id,
    )
    assert manifest["sessions"][0]["summary"]["stale"] is True
    assert manifest["sessions"][0]["summary"]["excerpt"] == "원본 기반 요약"
    projects.assign("event-1", None)
    without = build_project_archive_manifest(
        projects, history, archive, project.project_id,
    )
    assert without["sessions"] == []
    assert without["pagination"]["total_sessions_at_query"] == 0
    assert history.get_session("event-1") is not None
    assert archive.latest_summary("event-1")["summary_id"] == draft["summary_id"]


@pytest.mark.asyncio
async def test_archive_manifest_api_is_operator_only_and_rejects_unknown_project(
    tmp_path, monkeypatch,
) -> None:
    from langtextflow import main

    history, projects, archive = _seed(tmp_path)
    project = projects.create(ProjectInput(title="Restricted event"))
    _session(history, project.project_id, "event-1", "행사 원문")
    monkeypatch.setattr(main, "project_repository", projects)
    monkeypatch.setattr(main, "post_event_archive", archive)
    monkeypatch.setattr(main, "runtime", SimpleNamespace(history=history))
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    remote = SimpleNamespace(client=SimpleNamespace(host="192.168.0.50"), headers={})
    result = await main.project_archive_manifest(
        local, project.project_id, limit=25, offset=0,
    )
    assert len(result["sessions"]) == 1
    with pytest.raises(HTTPException) as denied:
        await main.project_archive_manifest(remote, project.project_id, limit=25, offset=0)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await main.project_archive_manifest(local, "missing", limit=25, offset=0)
    assert missing.value.status_code == 404
    with pytest.raises(ValueError, match="pagination"):
        projects.session_ids(project.project_id, limit=0)
