"""Deterministic project digest from verified source references; no new LLM call."""
from __future__ import annotations

import html

from .post_event_archive import PostEventArchive
from .post_event_summary import transcript_source
from .project_archive import build_project_archive_manifest
from .project_repository import ProjectRepository
from .session_repository import SessionRepository


def _plain(value: object, *, limit: int = 300) -> str:
    """Render untrusted titles/transcript/model output as inert Markdown text."""
    normalized = " ".join(str(value or "").split())[:limit]
    escaped = html.escape(normalized, quote=False)
    markdown_special = set("\\`*_{}[]()#+-.!>|~")
    return "".join("\\" + char if char in markdown_special else char for char in escaped)


def _timestamp(ms: int) -> str:
    seconds = max(0, ms // 1000)
    return f"{seconds // 3600:02d}:{(seconds // 60) % 60:02d}:{seconds % 60:02d}"


def build_project_report(
    projects: ProjectRepository,
    sessions: SessionRepository,
    archive: PostEventArchive,
    project_id: str,
    *,
    limit: int = 10,
    offset: int = 0,
) -> str:
    """Bounded human-readable report retaining session/segment provenance.

    Never aggregates draft transcripts or pretends an unreviewed model summary
    is correct. Older summaries remain visible, but visibly flagged as stale.
    """
    if not 1 <= limit <= 25:
        raise ValueError("report page size must be 1–25")
    index = build_project_archive_manifest(
        projects, sessions, archive, project_id,
        limit=limit, offset=offset,
    )
    project = index["project"]
    pages = index["pagination"]
    lines = [
        f"# Event report: {_plain(project['title'], limit=120)}",
        "",
        "Status: operator-only, generated from stored sessions; NOT a verified AI synthesis.",
        f"Event project: `{_plain(project_id, limit=64)}`",
        f"Generated at (UTC): {index['generated_at']}",
        f"Sessions in this page: {len(index['sessions'])}",
        f"Total sessions at query time: {pages['total_sessions_at_query']}",
        f"Page offset: {offset}; next page: {'yes' if pages['has_more'] else 'no'}",
        "",
    ]
    for item in index["sessions"]:
        sid = item["session_id"]
        # Filter again: reassigning between page lookup and transcript read
        # must not expose cross-project source content.
        session = sessions.get_session(sid)
        if session is None or session.project_id != project_id:
            continue
        lines += [
            f"## {_plain(item['title'], limit=120)}",
            "",
            f"- Session: `{_plain(sid, limit=64)}`",
            f"- Started: {item['started_at']}",
            f"- Recording files: {item['recording_count']}",
            f"- Transcript segments: {item['segment_count']}",
            f"- Interrupted: {'yes' if item['interrupted'] else 'no'}",
        ]
        summary = archive.latest_summary(sid)
        if summary is None:
            lines.append("- Summary: not generated")
        else:
            try:
                _, current_hash = transcript_source(sessions.segments(sid))
                stale = current_hash != summary["transcript_sha256"]
            except ValueError:
                stale = True
            lines += [
                f"- Summary version: `{_plain(summary['summary_id'], limit=64)}`",
                f"- Summary source SHA-256: `{summary['transcript_sha256']}`",
                (
                    "- Summary freshness: "
                    + ("STALE — re-review required" if stale else "source revision unchanged — NOT fact-checked")
                ),
                f"- Summary status: {summary['status']}",
            ]
            if summary["status"] == "completed":
                lines += [
                    "",
                    "### Existing session summary (unreviewed)",
                    "",
                    _plain(summary["content"], limit=5000),
                ]
        committed = [
            segment for segment in sessions.segments(sid)
            if segment.committed and segment.text.strip()
        ]
        if committed:
            lines += ["", "### Original transcript evidence (selected excerpts)", ""]
            for segment in committed[:3]:
                lines.append(
                    f"- `{_plain(segment.segment_id, limit=128)}` "
                    f"at {_timestamp(segment.start_ms)}: "
                    f"{_plain(segment.text, limit=380)}"
                )
            if len(committed) > 3:
                lines.append(
                    f"- … {len(committed) - 3} more committed segments in the session archive"
                )
        lines += [
            "",
            "Source: use the session ID and segment ID to review complete original captions.",
            "",
        ]
    lines += [
        "---",
        "This is a page-scoped working report, not a full project backup. "
        "Audio, translations and complete transcripts are not embedded.",
        "Check recording consent, summary provenance and transcript revisions before distribution.",
        "",
    ]
    return "\n".join(lines)
