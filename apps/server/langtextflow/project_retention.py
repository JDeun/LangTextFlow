"""Explicit local retention: bounded preview, atomic archive deletion, durable media ownership."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

MAX_RETENTION_SESSIONS = 100
MAX_RETENTION_MEDIA = 64
MAX_PROJECT_MEMBERS = 1000


class RetentionConflict(ValueError):
    pass


class RetentionStopped(Exception):
    pass


class RetentionCapacityError(ValueError):
    pass


class RetentionPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int | None = Field(default=None, ge=1, le=36500, strict=True)
    expected_revision: int = Field(ge=0, strict=True)


class RetentionApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    expected_revision: int = Field(ge=1, strict=True)
    targets_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return (
            parsed.astimezone(UTC)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None
            else None
        )
    except (ValueError, OverflowError):
        return None


class ProjectRetention:
    def __init__(self, archive):
        self.archive = archive

    def initialize(self):
        with self.archive._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS project_retention_revisions (
                    project_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK(revision > 0)
                );
                CREATE TABLE IF NOT EXISTS project_retention (
                    project_id TEXT PRIMARY KEY REFERENCES projects(project_id) ON DELETE CASCADE,
                    days INTEGER CHECK(days IS NULL OR days BETWEEN 1 AND 36500),
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_retention_receipts (
                    operation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    policy_revision INTEGER NOT NULL,
                    targets_sha256 TEXT NOT NULL,
                    deleted_session_count INTEGER NOT NULL,
                    recording_count INTEGER NOT NULL,
                    completed_at TEXT NOT NULL,
                    media_ids_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_retention_receipts
                    ON project_retention_receipts(project_id,completed_at);
                INSERT OR IGNORE INTO project_retention_revisions
                    SELECT project_id,revision FROM project_retention;
                CREATE TRIGGER IF NOT EXISTS invalidate_deleted_project_retention
                BEFORE DELETE ON projects BEGIN
                    INSERT INTO project_retention_revisions (project_id,revision)
                    VALUES (OLD.project_id, COALESCE(
                        (SELECT revision FROM project_retention
                         WHERE project_id=OLD.project_id),0)+1)
                    ON CONFLICT(project_id) DO UPDATE SET
                        revision=MAX(project_retention_revisions.revision,excluded.revision-1)+1;
                END;
            """)

    @staticmethod
    def _policy(conn, project_id):
        if not conn.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone():
            raise KeyError("project not found")
        row = conn.execute(
            "SELECT * FROM project_retention WHERE project_id=?", (project_id,)
        ).fetchone()
        if row:
            return dict(row)
        tombstone = conn.execute(
            "SELECT revision FROM project_retention_revisions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        return {
            "project_id": project_id,
            "days": None,
            "revision": tombstone[0] if tombstone else 0,
            "updated_at": None,
        }

    def policy(self, project_id):
        with self.archive._connect() as conn:
            return self._policy(conn, project_id)

    def save_policy(self, project_id, payload):
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._policy(conn, project_id)
            if current["revision"] != payload.expected_revision:
                raise RetentionConflict("Retention policy changed; reload and preview again")
            conn.execute(
                "INSERT INTO project_retention VALUES (?,?,?,?) ON CONFLICT(project_id) "
                "DO UPDATE SET days=excluded.days,revision=excluded.revision,"
                "updated_at=excluded.updated_at",
                (project_id, payload.days, current["revision"] + 1, datetime.now(UTC).isoformat()),
            )
            conn.execute(
                "INSERT INTO project_retention_revisions VALUES (?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision",
                (project_id, current["revision"] + 1),
            )
            return self._policy(conn, project_id)

    def _preview(self, conn, project_id, active_session_id=None):
        policy = self._policy(conn, project_id)
        now = datetime.now(UTC)
        cutoff = (now - timedelta(days=policy["days"])).isoformat() if policy["days"] else None

        def expired(ended, threshold):
            actual, boundary = _utc(ended), _utc(threshold)
            return int(actual is not None and boundary is not None and actual <= boundary)

        scope = conn.execute(
            "SELECT s.session_id,s.title,s.started_at,s.ended_at FROM sessions s "
            "JOIN project_sessions p ON p.session_id=s.session_id WHERE p.project_id=? "
            "ORDER BY s.session_id LIMIT ?",
            (project_id, MAX_PROJECT_MEMBERS + 1),
        ).fetchall()
        if len(scope) > MAX_PROJECT_MEMBERS:
            raise RetentionCapacityError(
                "Retention scope exceeds 1000 project sessions; reduce the project scope"
            )
        eligible = sorted(
            (
                row
                for row in scope
                if row["session_id"] != active_session_id and expired(row["ended_at"], cutoff)
            ),
            key=lambda row: (_utc(row["ended_at"]), row["session_id"]),
        )
        eligible_ids = _json([row["session_id"] for row in eligible])
        counts = dict(
            conn.execute(
                "SELECT session_id,SUM(n) FROM ("
                "SELECT session_id,COUNT(*) AS n FROM session_recordings "
                "WHERE session_id IN (SELECT value FROM json_each(?)) "
                "GROUP BY session_id UNION ALL "
                "SELECT r.session_id,COUNT(*) AS n FROM recording_journal r "
                "WHERE r.session_id IN (SELECT value FROM json_each(?)) "
                "AND NOT EXISTS (SELECT 1 FROM session_recordings a "
                "WHERE a.recording_id=r.recording_id) GROUP BY r.session_id) GROUP BY session_id",
                (eligible_ids, eligible_ids),
            ).fetchall()
        )
        rows, media_count = [], 0
        for row in eligible:
            count = counts.get(row["session_id"], 0)
            if not rows and count > MAX_RETENTION_MEDIA:
                raise RetentionCapacityError(
                    "The oldest expired session exceeds 64 recordings; review its manual "
                    "session deletion controls first. Retention never deletes part of a session"
                )
            if len(rows) >= MAX_RETENTION_SESSIONS or media_count + count > MAX_RETENTION_MEDIA:
                break
            rows.append(row)
            media_count += count
        selected_ids = _json([row["session_id"] for row in rows])
        # Include unregistered but durably owned prepared WAVs too. Their
        # session FK becomes a tombstone; unknown files are never scanned.
        media = conn.execute(
            "SELECT r.recording_id,r.session_id,r.filename,r.size_bytes,r.sha256,"
            "r.duration_seconds,"
            "r.sample_rate,r.channels,r.created_at,r.start_offset_ms FROM session_recordings r "
            "WHERE r.session_id IN (SELECT value FROM json_each(?)) UNION ALL "
            "SELECT r.recording_id,r.session_id,r.filename,r.size_bytes,r.sha256,"
            "r.duration_seconds,"
            "r.sample_rate,r.channels,r.created_at,r.start_offset_ms FROM recording_journal r "
            "WHERE r.session_id IN (SELECT value FROM json_each(?)) AND NOT EXISTS "
            "(SELECT 1 FROM session_recordings a WHERE a.recording_id=r.recording_id) "
            "ORDER BY recording_id LIMIT ?",
            (
                selected_ids,
                selected_ids,
                MAX_RETENTION_MEDIA + 1,
            ),
        ).fetchall()
        if len(media) != media_count or len(media) > MAX_RETENTION_MEDIA:
            raise RetentionConflict("Retention media scope changed; preview again")
        candidates = [
            {
                **dict(row),
                "recording_count": sum(r["session_id"] == row["session_id"] for r in media),
                "size_bytes": sum(
                    r["size_bytes"] for r in media if r["session_id"] == row["session_id"]
                ),
            }
            for row in rows
        ]
        records = []
        for row in rows:
            sid = row["session_id"]
            source = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(version),0),"
                "COALESCE(SUM(source_revision),0),MAX(emitted_at) "
                "FROM transcript_segments WHERE session_id=?",
                (sid,),
            ).fetchone()
            children = conn.execute(
                "SELECT (SELECT COUNT(*) FROM session_summaries WHERE session_id=?),"
                "(SELECT COUNT(*) FROM summary_reviews r JOIN session_summaries s "
                "ON s.summary_id=r.summary_id WHERE s.session_id=?),"
                "(SELECT COUNT(*) FROM transcript_versions WHERE session_id=?),"
                "(SELECT COUNT(*) FROM transcript_reviews r JOIN transcript_versions v "
                "ON v.transcript_id=r.transcript_id WHERE v.session_id=?)",
                (sid, sid, sid, sid),
            ).fetchone()
            records.append([sid, list(source), list(children)])
        fingerprint = {
            "policy_revision": policy["revision"],
            "days": policy["days"],
            "scope": [dict(row) for row in scope],
            "eligible_media_counts": [
                [r["session_id"], counts.get(r["session_id"], 0)] for r in eligible
            ],
            "records": records,
            "candidates": candidates,
            "media": [dict(row) for row in media],
        }
        result = {
            "policy": policy,
            "evaluated_at": now.isoformat(),
            "cutoff": cutoff,
            "candidates": candidates,
            "recording_count": len(media),
            "expired_session_count": len(eligible),
            "remaining_session_count": len(eligible) - len(rows),
            "remaining_recording_count": sum(counts.values()) - media_count,
            "excluded_running_count": sum(
                r["ended_at"] is None or r["session_id"] == active_session_id for r in scope
            ),
            "excluded_unverifiable_count": sum(
                r["ended_at"] is not None and _utc(r["ended_at"]) is None for r in scope
            ),
            "targets_sha256": hashlib.sha256(_json(fingerprint).encode()).hexdigest(),
        }
        return result, media

    def preview(self, project_id, active_session_id=None):
        with self.archive._connect() as conn:
            deadline = time.monotonic() + 10
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            conn.execute("BEGIN")
            try:
                return self._preview(conn, project_id, active_session_id)[0]
            except sqlite3.OperationalError:
                if time.monotonic() >= deadline:
                    raise TimeoutError from None
                raise
            finally:
                conn.set_progress_handler(None, 0)

    @staticmethod
    def _receipt(conn, row):
        result = dict(row)
        identifiers = result.pop("media_ids_json")
        pending, blocked = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(recovery_error IS NOT NULL "
            "AND recovery_error!='io_deferred'),0) "
            "FROM recording_journal WHERE recording_id IN (SELECT value FROM json_each(?))",
            (identifiers,),
        ).fetchone()
        return {
            **result,
            "media_cleanup_pending_count": pending,
            "media_cleanup_blocked_count": blocked,
        }

    def receipts(self, project_id):
        with self.archive._connect() as conn:
            self._policy(conn, project_id)
            return [
                self._receipt(conn, row)
                for row in conn.execute(
                    "SELECT * FROM project_retention_receipts WHERE project_id=? "
                    "ORDER BY rowid DESC LIMIT 20",
                    (project_id,),
                )
            ]

    def completed(self, project_id, payload):
        with self.archive._connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_retention_receipts WHERE operation_id=?",
                (payload.operation_id,),
            ).fetchone()
            if row is None:
                return None
            if (
                row["project_id"] != project_id
                or row["policy_revision"] != payload.expected_revision
                or row["targets_sha256"] != payload.targets_sha256
            ):
                raise RetentionConflict("Retention operation identity changed")
            return self._receipt(conn, row)

    def apply(self, project_id, payload, active_session_id, stop, deadline):
        def check():
            if stop.is_set():
                raise RetentionStopped
            if time.monotonic() >= deadline:
                raise TimeoutError

        with self.archive._connect() as conn:
            conn.set_progress_handler(
                lambda: int(stop.is_set() or time.monotonic() >= deadline), 1000
            )
            try:
                check()
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute(
                    "SELECT * FROM project_retention_receipts WHERE operation_id=?",
                    (payload.operation_id,),
                ).fetchone()
                if previous:
                    if (
                        previous["project_id"] != project_id
                        or previous["policy_revision"] != payload.expected_revision
                        or previous["targets_sha256"] != payload.targets_sha256
                    ):
                        raise RetentionConflict("Retention operation identity changed")
                    return self._receipt(conn, previous)
                preview, media = self._preview(conn, project_id, active_session_id)
                if (
                    preview["policy"]["revision"] != payload.expected_revision
                    or preview["targets_sha256"] != payload.targets_sha256
                ):
                    raise RetentionConflict("Retention policy or candidates changed; preview again")
                if not preview["policy"]["days"] or not preview["candidates"]:
                    raise RetentionConflict("No expired sessions in the reviewed project scope")
                for row in media:
                    self.archive.recording_journal._check_metadata(row)
                    # Existing registered metadata proves the expected immutable
                    # identity, not disk contents. Recovery verifies bytes before
                    # removing anything, preserving foreign/corrupt assets.
                    conn.execute(
                        "INSERT OR IGNORE INTO recording_journal "
                        "(recording_id,session_id,owner_session_id,filename,size_bytes,sha256,duration_seconds,"
                        "sample_rate,channels,created_at,start_offset_ms,registered) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                        (
                            row["recording_id"],
                            row["session_id"],
                            row["session_id"],
                            row["filename"],
                            row["size_bytes"],
                            row["sha256"],
                            row["duration_seconds"],
                            row["sample_rate"],
                            row["channels"],
                            row["created_at"],
                            row["start_offset_ms"],
                        ),
                    )
                    owner = conn.execute(
                        "SELECT * FROM recording_journal WHERE recording_id=?",
                        (row["recording_id"],),
                    ).fetchone()
                    if (
                        owner is None
                        or any(
                            owner[key] != row[key]
                            for key in (
                                "session_id",
                                "filename",
                                "sha256",
                                "size_bytes",
                                "duration_seconds",
                                "sample_rate",
                                "channels",
                                "start_offset_ms",
                            )
                        )
                        or owner["owner_session_id"] != row["session_id"]
                    ):
                        raise RetentionConflict(
                            "Recording ownership changed; review archive diagnostics"
                        )
                check()
                for item in preview["candidates"]:
                    check()
                    conn.execute("DELETE FROM sessions WHERE session_id=?", (item["session_id"],))
                result = {
                    "operation_id": payload.operation_id,
                    "project_id": project_id,
                    "policy_revision": payload.expected_revision,
                    "targets_sha256": payload.targets_sha256,
                    "deleted_session_count": len(preview["candidates"]),
                    "recording_count": len(media),
                    "completed_at": datetime.now(UTC).isoformat(),
                }
                conn.execute(
                    "INSERT INTO project_retention_receipts VALUES (?,?,?,?,?,?,?,?)",
                    (*result.values(), _json([r["recording_id"] for r in media])),
                )
                conn.execute(
                    "DELETE FROM project_retention_receipts WHERE project_id=? "
                    "AND operation_id NOT IN (SELECT operation_id FROM project_retention_receipts "
                    "WHERE project_id=? ORDER BY rowid DESC LIMIT 20)",
                    (project_id, project_id),
                )
                check()
                conn.commit()
                return self._receipt(
                    conn,
                    conn.execute(
                        "SELECT * FROM project_retention_receipts WHERE operation_id=?",
                        (payload.operation_id,),
                    ).fetchone(),
                )
            except sqlite3.OperationalError:
                conn.set_progress_handler(None, 0)
                conn.rollback()
                check()
                raise
            finally:
                conn.set_progress_handler(None, 0)
