"""Immutable, bounded project synthesis evidence and operator reviews."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from .models import CaptionStage, TranscriptEvent
from .post_event_archive import _now
from .post_event_summary import MAX_SOURCE_CHARS
from .summary_evidence import validate_summary_citations

PROJECT_PROMPT_VERSION = "project-v1-chronology-original-evidence"
MAX_PROJECT_SESSIONS = 25
MAX_PROJECT_SEGMENTS = 6000
MAX_PROJECT_VERSIONS = 50
MAX_PROJECT_REVIEWS = 100


class DigestCapacityError(ValueError):
    pass


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_project_citations(snapshot, content):
    markers = set(validate_summary_citations(snapshot["source"], content))
    cited_sessions = {
        item["session_id"] for item in snapshot["citations"] if item["marker"] in markers
    }
    if cited_sessions != {entry["session_id"] for entry in snapshot["sessions"]}:
        raise ValueError("Project digest must cite original evidence from every included session")


class ProjectDigests:
    def __init__(self, archive):
        self.archive = archive

    def initialize(self):
        with self.archive._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS project_digests (
                    digest_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    input_sha256 TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    language TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','error')),
                    content TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_digests
                    ON project_digests(project_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_project_digest_running
                    ON project_digests(project_id) WHERE status='running';
                CREATE TABLE IF NOT EXISTS project_digest_reviews (
                    review_id TEXT PRIMARY KEY,
                    digest_id TEXT NOT NULL REFERENCES project_digests(digest_id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    approved INTEGER NOT NULL CHECK(approved IN (0,1)),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_digest_reviews
                    ON project_digest_reviews(digest_id);
                CREATE TABLE IF NOT EXISTS project_digest_sessions (
                    digest_id TEXT NOT NULL REFERENCES project_digests(digest_id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    PRIMARY KEY(digest_id, session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_digest_sessions
                    ON project_digest_sessions(session_id);
                CREATE TRIGGER IF NOT EXISTS delete_session_project_digests
                BEFORE DELETE ON sessions BEGIN
                    DELETE FROM project_digests WHERE digest_id IN (
                        SELECT digest_id FROM project_digest_sessions
                        WHERE session_id=OLD.session_id
                    );
                END;
            """)
            conn.execute(
                "UPDATE project_digests SET status='error',error=?,updated_at=? "
                "WHERE status='running'",
                ("Project generation interrupted by restart; retry explicitly", _now()),
            )

    def snapshot(self, project_id: str, *, connection=None) -> dict:
        if connection is None:
            with self.archive._connect() as conn:
                conn.execute("BEGIN")
                return self.snapshot(project_id, connection=conn)
        conn = connection
        project = conn.execute(
            "SELECT * FROM projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if project is None:
            raise KeyError("project not found")
        # Read the complete scope, never quietly summarize the first archive page.
        sessions = conn.execute(
            "SELECT s.session_id,s.title,s.started_at,s.ended_at,s.interrupted,s.source_language "
            "FROM project_sessions p JOIN sessions s ON s.session_id=p.session_id "
            "WHERE p.project_id=? ORDER BY julianday(s.started_at),s.session_id LIMIT ?",
            (project_id, MAX_PROJECT_SESSIONS + 1),
        ).fetchall()
        if len(sessions) > MAX_PROJECT_SESSIONS:
            raise ValueError(
                "Project digest supports at most 25 sessions; reduce the project scope"
            )
        completed = [row for row in sessions if row["ended_at"] is not None]
        if not completed:
            raise ValueError("Finish a session and generate its current cited summary first")
        evidence: list[str] = []
        references: list[dict] = []
        entries = []
        notes = [{"project_metadata": dict(project)}]
        source_size = 0
        for number, row in enumerate(completed, 1):
            sid = row["session_id"]
            summary_row = conn.execute(
                "SELECT * FROM session_summaries WHERE session_id=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (sid,),
            ).fetchone()
            if summary_row is None or summary_row["status"] != "completed":
                raise ValueError("Every finished session needs a completed, current cited summary")
            summary = dict(summary_row)
            approved_id = self.archive.transcripts.approved_id(sid, connection=conn)
            if approved_id != summary["approved_transcript_id"]:
                raise ValueError(
                    "A session summary is stale; regenerate it before the project digest"
                )
            # Cursor iteration bounds live-source memory before materializing segments.
            live = []
            for segment in conn.execute(
                "SELECT segment_id,text,original_text,start_ms,end_ms,speaker,source_language,"
                "source_revision FROM transcript_segments WHERE session_id=? AND committed=1 "
                "ORDER BY start_ms,segment_id",
                (sid,),
            ):
                source_size += len(segment["original_text"] or segment["text"]) + 160
                if (
                    len(live) + len(references) >= MAX_PROJECT_SEGMENTS
                    or source_size > MAX_SOURCE_CHARS
                ):
                    raise ValueError("Project evidence exceeds 6000 segments or 240000 characters")
                live.append(
                    TranscriptEvent(
                        segment_id=segment["segment_id"],
                        text=segment["text"],
                        original_text=segment["original_text"],
                        start_ms=segment["start_ms"],
                        end_ms=segment["end_ms"],
                        speaker=segment["speaker"],
                        source_language=segment["source_language"],
                        source_revision=segment["source_revision"],
                        version=1,
                        stage=CaptionStage.COMMITTED,
                        committed=True,
                    )
                )
            source, source_hash = self.archive.transcripts.source(
                sid,
                live,
                summary["transcript_id"],
                connection=conn,
            )
            if source_hash != summary["transcript_sha256"]:
                raise ValueError(
                    "A session summary is stale; regenerate it before the project digest"
                )
            review = conn.execute(
                "SELECT * FROM summary_reviews WHERE summary_id=? ORDER BY rowid DESC LIMIT 1",
                (summary["summary_id"],),
            ).fetchone()
            content = review["content"] if review else summary["content"]
            validate_summary_citations(source, content)
            markers = {}
            for index, line in enumerate(source.splitlines(), 1):
                match = re.match(r"\[([A-Za-z0-9][A-Za-z0-9_.:-]{0,127}) at (\d{1,7})s\]", line)
                if match is None:
                    raise ValueError("Invalid session source citation")
                alias = f"p{number:04}.s{index:06}"
                marker = f"[{alias} at {match[2]}s]"
                markers[match[0]] = marker
                evidence.append(marker + line[len(match[0]) :])
                references.append(
                    {
                        "alias": alias,
                        "marker": marker,
                        "session_id": sid,
                        "segment_id": match[1],
                        "start_ms": int(match[2]) * 1000,
                        "transcript_id": summary["transcript_id"],
                    }
                )
                if len(references) > MAX_PROJECT_SEGMENTS:
                    raise ValueError("Project evidence exceeds 6000 segments or 240000 characters")
            # Existing notes never become independently citable spoken evidence.
            remapped = re.sub(
                r"\[[A-Za-z0-9][A-Za-z0-9_.:-]{0,127} at \d{1,7}s\]",
                lambda match, mapping=markers: mapping[match[0]],
                content,
            )
            entry = {
                **dict(row),
                "source_sha256": source_hash,
                "citation_prefix": f"p{number:04}.",
                "transcript_id": summary["transcript_id"],
                "approved_transcript_id": approved_id,
                "source_revisions": [
                    [item.segment_id, item.source_revision, item.start_ms, item.end_ms]
                    for item in live
                ],
                "summary": {
                    key: summary[key]
                    for key in (
                        "summary_id",
                        "provider",
                        "model",
                        "language",
                        "prompt_version",
                        "transcript_sha256",
                        "created_at",
                    )
                },
                "review": {
                    key: review[key]
                    for key in (
                        "review_id",
                        "approved",
                        "created_at",
                    )
                }
                if review
                else None,
            }
            entries.append(entry)
            notes.append({"session_metadata": entry, "existing_session_summary_notes": remapped})
        source = "\n".join(evidence)
        note_text = _json(notes)
        if len(source) + len(note_text) > MAX_SOURCE_CHARS:
            raise ValueError("Project evidence exceeds 240000 characters; reduce the project scope")
        snapshot = {
            "project": dict(project),
            "sessions": entries,
            "excluded_running_session_ids": [
                row["session_id"] for row in sessions if row["ended_at"] is None
            ],
            "source": source,
            "notes": note_text,
            "citations": references,
        }
        return {**snapshot, "input_sha256": hashlib.sha256(_json(snapshot).encode()).hexdigest()}

    @staticmethod
    def _scoped(conn, project_id, snapshot):
        return all(
            conn.execute(
                "SELECT 1 FROM project_sessions WHERE project_id=? AND session_id=?",
                (project_id, entry["session_id"]),
            ).fetchone()
            for entry in snapshot["sessions"]
        )

    @staticmethod
    def _record(conn, row):
        if row is None:
            return None
        result = dict(row)
        result["snapshot"] = json.loads(result.pop("snapshot_json"))
        review = conn.execute(
            "SELECT * FROM project_digest_reviews WHERE digest_id=? ORDER BY rowid DESC LIMIT 1",
            (row["digest_id"],),
        ).fetchone()
        result["review"] = dict(review) if review else None
        if result["review"]:
            result["review"]["approved"] = bool(result["review"]["approved"])
        return result

    def begin(self, project_id, payload, model, *, allow_new=True, ensure_space=None):
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = self.snapshot(project_id, connection=conn)
            previous = conn.execute(
                "SELECT * FROM project_digests WHERE project_id=? AND input_sha256=? "
                "AND language=? AND provider=? AND model=? AND prompt_version=? "
                "AND status='completed' ORDER BY rowid DESC LIMIT 1",
                (
                    project_id,
                    snapshot["input_sha256"],
                    payload.language,
                    payload.provider,
                    model,
                    PROJECT_PROMPT_VERSION,
                ),
            ).fetchone()
            if previous:
                return self._record(conn, previous), False
            if conn.execute(
                "SELECT 1 FROM project_digests WHERE project_id=? AND status='running'",
                (project_id,),
            ).fetchone():
                raise ValueError("Project generation already running")
            if not allow_new:
                raise DigestCapacityError(
                    "summary capacity reached; retry after another job finishes"
                )
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM project_digests WHERE project_id=?", (project_id,)
                ).fetchone()[0]
                >= MAX_PROJECT_VERSIONS
            ):
                raise ValueError("Project digest version limit reached")
            identifier, stamp = str(uuid4()), _now()
            if ensure_space is not None:
                ensure_space()
            conn.execute(
                "INSERT INTO project_digests VALUES (?,?,?,?,?,?,?,?,'running','',NULL,?,?)",
                (
                    identifier,
                    project_id,
                    snapshot["input_sha256"],
                    _json(snapshot),
                    payload.language,
                    payload.provider,
                    model,
                    PROJECT_PROMPT_VERSION,
                    stamp,
                    stamp,
                ),
            )
            conn.executemany(
                "INSERT INTO project_digest_sessions VALUES (?,?)",
                [(identifier, entry["session_id"]) for entry in snapshot["sessions"]],
            )
            row = conn.execute(
                "SELECT * FROM project_digests WHERE digest_id=?", (identifier,)
            ).fetchone()
            return self._record(conn, row), True

    def finish(self, identifier, *, content="", error=None):
        with self.archive._connect() as conn:
            conn.execute(
                "UPDATE project_digests SET status=?,content=?,error=?,updated_at=? "
                "WHERE digest_id=? AND status='running'",
                ("error" if error else "completed", content, error, _now(), identifier),
            )

    def evidence_present(self, project_id, identifier, expected_sessions):
        """Revoke further provider sends after source deletion or membership change."""
        with self.archive._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT 1 FROM project_digests WHERE project_id=? AND digest_id=? "
                "AND status='running'",
                (project_id, identifier),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) FROM project_digest_sessions d JOIN project_sessions p "
                "ON p.session_id=d.session_id WHERE d.digest_id=? AND p.project_id=?",
                (identifier, project_id),
            ).fetchone()[0]
            return row is not None and count == expected_sessions

    def listing(self, project_id):
        with self.archive._connect() as conn:
            conn.execute("BEGIN")
            if not conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
            ).fetchone():
                raise KeyError("project not found")
            try:
                current = self.snapshot(project_id, connection=conn)
                error = None
            except ValueError as exc:
                current, error = None, str(exc)
            rows = conn.execute(
                "SELECT * FROM project_digests WHERE project_id=? ORDER BY rowid DESC LIMIT ?",
                (project_id, MAX_PROJECT_VERSIONS),
            ).fetchall()
            versions = []
            for row in rows:
                record = self._record(conn, row)
                record["session_count"] = len(record["snapshot"]["sessions"])
                record["stale"] = (
                    current is None or current["input_sha256"] != record["input_sha256"]
                )
                record.pop("snapshot")
                record.pop("content")
                if record["review"]:
                    record["review"].pop("content")
                versions.append(record)
            return {
                "versions": versions,
                "input": {
                    "input_sha256": current["input_sha256"],
                    "session_count": len(current["sessions"]),
                    "excluded_running_count": len(current["excluded_running_session_ids"]),
                    "interrupted_count": sum(
                        bool(entry["interrupted"]) for entry in current["sessions"]
                    ),
                }
                if current
                else None,
                "input_error": error,
            }

    def get(self, project_id, identifier):
        with self.archive._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM project_digests WHERE project_id=? AND digest_id=?",
                (project_id, identifier),
            ).fetchone()
            result = self._record(conn, row)
            if result is None:
                raise KeyError("project digest not found")
            try:
                current = self.snapshot(project_id, connection=conn)
                result["stale"] = current["input_sha256"] != result["input_sha256"]
            except ValueError:
                result["stale"] = True
            result["evidence_available"] = self._scoped(conn, project_id, result["snapshot"])
            if not result["evidence_available"]:
                result["snapshot"] = None
                result["content"] = ""
                result["review"] = None
            return result

    def review(self, project_id, identifier, payload):
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM project_digests WHERE project_id=? AND digest_id=?",
                (project_id, identifier),
            ).fetchone()
            record = self._record(conn, row)
            if record is None:
                raise KeyError("project digest not found")
            if record["status"] != "completed":
                raise ValueError("Only completed project drafts can be reviewed")
            try:
                current = self.snapshot(project_id, connection=conn)
            except ValueError as exc:
                raise ValueError("Project evidence changed; generate a new version") from exc
            if current["input_sha256"] != record["input_sha256"]:
                raise ValueError("Project evidence changed; generate a new version")
            previous = record["review"]
            if previous and previous["approved"]:
                raise ValueError("Approved project digest is locked")
            if (previous["review_id"] if previous else None) != payload.expected_review_id:
                raise ValueError("Project review changed; refresh before saving")
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM project_digest_reviews WHERE digest_id=?", (identifier,)
                ).fetchone()[0]
                >= MAX_PROJECT_REVIEWS
            ):
                raise ValueError("Project review limit reached")
            validate_project_citations(record["snapshot"], payload.content)
            review = {
                "review_id": str(uuid4()),
                "digest_id": identifier,
                "content": payload.content.strip(),
                "approved": payload.approved,
                "created_at": _now(),
            }
            conn.execute(
                "INSERT INTO project_digest_reviews VALUES (?,?,?,?,?)", tuple(review.values())
            )
            return review

    def export(self, project_id, identifier):
        record = self.get(project_id, identifier)
        if not record["evidence_available"]:
            raise ValueError("Project membership changed; regenerate before exporting")
        with self.archive._connect() as conn:
            reviews = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_digest_reviews WHERE digest_id=? ORDER BY rowid",
                    (identifier,),
                )
            ]
        return {
            "schema_version": 1,
            "kind": "project-digest-version",
            "version": record,
            "reviews": reviews,
            "limitations": (
                "Citations establish source identity, not semantic accuracy; not a project backup"
            ),
        }
