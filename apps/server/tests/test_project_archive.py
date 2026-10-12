"""Event-wide archive index: scoped provenance, pagination, stale detection."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from langtextflow.models import (
    CaptionStage,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_summary import transcript_source
from langtextflow.project_archive import build_project_archive_manifest
from langtextflow.project_report import _plain, build_project_report
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


def test_manifest_omits_sessions_reassigned_after_initial_page_query(
    tmp_path, monkeypatch,
) -> None:
    history, projects, archive = _seed(tmp_path)
    first = projects.create(ProjectInput(title="Original event"))
    second = projects.create(ProjectInput(title="New owner"))
    _session(history, first.project_id, "moving-session", "관리 대상 원문")
    original_ids = projects.session_ids

    def move_after_listing(project_id, *, limit=25, offset=0):
        ids = original_ids(project_id, limit=limit, offset=offset)
        projects.assign("moving-session", second.project_id)
        return ids

    monkeypatch.setattr(projects, "session_ids", move_after_listing)
    result = build_project_archive_manifest(
        projects, history, archive, first.project_id,
    )
    assert result["sessions"] == []
    assert history.get_session("moving-session").project_id == second.project_id



def test_report_compiles_original_evidence_and_flags_stale_summary(tmp_path) -> None:
    history, projects, archive = _seed(tmp_path)
    project = projects.create(ProjectInput(title="Community <script>alert(1)</script>"))
    private = projects.create(ProjectInput(title="Confidential"))
    _session(history, project.project_id, "public-session", "첫 발표 원문")
    _session(history, private.project_id, "private-session", "비공개 프로젝트 원문")
    _, fingerprint = transcript_source(history.segments("public-session"))
    initial = archive.begin_summary(
        "public-session", language="ko", provider="ollama", model="test-local",
        transcript_sha256=fingerprint, prompt_version="post-event-v1",
    )
    archive.finish_summary(
        initial["summary_id"], content="AI가 쓴 <b>요약</b> [검토 전] 문장",
    )
    # The completed transcript changed after the summary was generated.
    history.upsert_segment(
        "public-session",
        TranscriptEvent(
            segment_id="speech", version=2, stage=CaptionStage.COMMITTED,
            committed=True, source_language="ko",
            text="금요일까지 후속 검토 <script>금지</script>.", start_ms=2500,
        ),
    )
    report = build_project_report(projects, history, archive, project.project_id)
    assert "STALE" in report
    assert initial["summary_id"] in report
    assert "00:00:02" in report
    assert "speech" in report
    assert "금요일까지 후속 검토" in report
    assert "AI가 쓴 &lt;b&gt;요약&lt;/b&gt;" in report
    assert "Community &lt;script&gt;" in report
    assert "<script>" not in report
    assert "<b>" not in report
    assert "비공개 프로젝트 원문" not in report
    assert "private-session" not in report
    assert str(tmp_path) not in report

    projects.assign("public-session", None)
    unassigned = build_project_report(projects, history, archive, project.project_id)
    assert "public-session" not in unassigned
    assert "Sessions in this page: 0" in unassigned


@pytest.mark.asyncio
async def test_report_api_is_operator_only_bounded_and_downloadable(
    tmp_path, monkeypatch,
) -> None:
    from langtextflow import main

    history, projects, archive = _seed(tmp_path)
    project = projects.create(ProjectInput(title="Conference materials"))
    _session(history, project.project_id, "one", "행사 최종 전사")
    monkeypatch.setattr(main, "project_repository", projects)
    monkeypatch.setattr(main, "post_event_archive", archive)
    monkeypatch.setattr(main, "runtime", SimpleNamespace(history=history))
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    remote = SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"), headers={})
    result = await main.download_project_report(
        local, project.project_id, limit=10, offset=0,
    )
    assert result.status_code == 200
    assert "text/markdown" in result.media_type
    assert b"one" in result.body
    assert "attachment" in result.headers["content-disposition"]
    assert result.headers["cache-control"] == "no-store"
    with pytest.raises(HTTPException) as denied:
        await main.download_project_report(
            remote, project.project_id, limit=10, offset=0,
        )
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await main.download_project_report(local, "absent", limit=10, offset=0)
    assert missing.value.status_code == 404
    with pytest.raises(ValueError):
        build_project_report(projects, history, archive, project.project_id, limit=26)


@pytest.mark.parametrize(
    ("untrusted", "forbidden"),
    [
        ("**model claims approval**", "**model"),
        ("[click](javascript:alert(1))", "[click]("),
        ("# synthetic official heading", "# synthetic"),
        ("`model generated evidence`", "`model generated evidence`"),
        ("<img src=x onerror=alert(1)>", "<img"),
    ],
)
def test_report_escapes_untrusted_markdown_syntax(
    untrusted: str, forbidden: str,
) -> None:
    rendered = _plain(untrusted)
    if forbidden == "# synthetic":
        # Markdown escaped leading hash is inert despite containing the substring.
        assert rendered.startswith(r"\# synthetic")
    else:
        assert forbidden not in rendered
    assert "\\n" not in rendered
    assert "\\r" not in rendered


def test_report_escapes_untrusted_ai_summary_without_link_or_heading(tmp_path) -> None:
    history, projects, archive = _seed(tmp_path)
    project = projects.create(ProjectInput(title="# Official event"))
    _session(history, project.project_id, "event-1", "[danger](javascript:alert(1))")
    _, original_hash = transcript_source(history.segments("event-1"))
    summary = archive.begin_summary(
        "event-1", language="ko", provider="ollama", model="local",
        transcript_sha256=original_hash, prompt_version="post-event-v1",
    )
    archive.finish_summary(
        summary["summary_id"],
        content="**Approved** [download](javascript:alert(1))",
    )
    report = build_project_report(projects, history, archive, project.project_id)
    assert r"\# Official event" in report
    assert r"\*\*Approved\*\*" in report
    assert r"\[download\]\(javascript:alert\(1\)\)" in report
    assert "[download](javascript:" not in report
    assert "summary (unreviewed)" in report
