"""Bounded project archive manifest: pointers and provenance, not copied audio."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .post_event_archive import PostEventArchive
from .post_event_summary import transcript_source
from .project_repository import ProjectRepository
from .session_repository import SessionRepository


def build_project_archive_manifest(
    projects: ProjectRepository,
    sessions: SessionRepository,
    archive: PostEventArchive,
    project_id: str,
    *,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Build operator-only index of original sessions with reproducible references.

    Only existing metadata is included. A summary excerpt is *not* a new AI
    synthesis; consumers must respect stale, interrupted and review statuses.
    No raw audio, local filesystem paths or API tokens are serialized.
    """
    project = projects.get(project_id)
    if project is None:
        raise KeyError("project not found")
    ids = projects.session_ids(project_id, limit=limit, offset=offset)
    items: list[dict[str, Any]] = []
    for session_id in ids:
        session = sessions.get_session(session_id)
        if session is None:
            # Another request deleted a session after the ID list was fetched.
            continue
        summary = archive.latest_summary(session_id)
        recordings = archive.recordings(session_id)
        stale = False
        if summary is not None:
            try:
                _, current = transcript_source(sessions.segments(session_id))
                stale = current != summary["transcript_sha256"]
            except ValueError:
                stale = True
        items.append(
            {
                "session_id": session_id,
                "title": session.title,
                "started_at": session.started_at.isoformat(),
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "interrupted": session.interrupted,
                "source_language": session.source_language,
                "target_languages": list(session.target_languages),
                "segment_count": session.segment_count,
                "recording_count": len(recordings),
                "recording_duration_seconds": round(
                    sum(float(item["duration_seconds"]) for item in recordings), 3
                ),
                "recordings": [
                    {
                        "recording_id": item["recording_id"],
                        "sha256": item["sha256"],
                        "duration_seconds": item["duration_seconds"],
                        "size_bytes": item["size_bytes"],
                    }
                    for item in recordings
                ],
                "summary": (
                    {
                        "summary_id": summary["summary_id"],
                        "language": summary["language"],
                        "status": summary["status"],
                        "model": summary["model"],
                        "provider": summary["provider"],
                        "transcript_sha256": summary["transcript_sha256"],
                        "prompt_version": summary["prompt_version"],
                        "stale": stale,
                        "excerpt": summary["content"][:400],
                        "created_at": summary["created_at"],
                    }
                    if summary is not None else None
                ),
            }
        )
    return {
        "schema_version": 1,
        "manifest_kind": "event-project-archive-index",
        "generated_at": datetime.now(UTC).isoformat(),
        "project": project.model_dump(mode="json"),
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total_sessions_at_query": project.session_count,
            "has_more": offset + len(ids) < project.session_count,
        },
        "sessions": items,
        "limitations": (
            "Metadata and latest per-session summaries only; no audio or full transcripts. "
            "This is not an independently verified project-level AI synthesis."
        ),
    }
