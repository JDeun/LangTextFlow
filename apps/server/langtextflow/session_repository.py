from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    CorrectionProvenance,
    ProductPreset,
    SessionContext,
    SessionDetail,
    SessionRecord,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)


class SessionRepository:
    """SQLite-backed session history with latest-version transcript upserts."""

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    join_code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    presenter TEXT,
                    preset TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    target_languages_json TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    correction_provider TEXT NOT NULL DEFAULT 'none',
                    correction_model TEXT,
                    translation_provider TEXT NOT NULL,
                    translation_model TEXT,
                    context_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    interrupted INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            session_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "notes" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN notes TEXT NOT NULL DEFAULT ''"
                )
            if "correction_provider" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN correction_provider "
                    "TEXT NOT NULL DEFAULT 'none'"
                )
            if "correction_model" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN correction_model TEXT"
                )
            if "interrupted" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcript_segments (
                    session_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    text TEXT NOT NULL,
                    translations_json TEXT NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER,
                    speaker TEXT,
                    confidence REAL,
                    correction_json TEXT,
                    committed INTEGER NOT NULL,
                    emitted_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, segment_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
                """
            )
            segment_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(transcript_segments)"
                ).fetchall()
            }
            if "correction_json" not in segment_columns:
                connection.execute(
                    "ALTER TABLE transcript_segments ADD COLUMN correction_json TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at DESC)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_segments_session_start
                ON transcript_segments(session_id, start_ms)
                """
            )

    def recover_interrupted_sessions(self, recovered_at: datetime | None = None) -> int:
        """Close sessions left open by a previous process termination.

        This is called once during runtime initialization, before a new live session can
        start. It never rewrites transcript content or the original Session Context.
        """

        timestamp = (recovered_at or datetime.now(UTC)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET ended_at = ?, interrupted = 1
                WHERE ended_at IS NULL
                """,
                (timestamp,),
            )
        return cursor.rowcount

    def create_session(self, state: SessionState, request: StartSessionRequest) -> None:
        if not state.session_id or not state.join_code or not state.started_at:
            raise ValueError("session state is incomplete")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, join_code, title, presenter, preset,
                    source_language, target_languages_json, engine,
                    correction_provider, correction_model,
                    translation_provider, translation_model, context_json,
                    started_at, ended_at, interrupted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                (
                    state.session_id,
                    state.join_code,
                    request.context.title,
                    request.context.presenter,
                    request.context.preset.value,
                    request.source_language,
                    json.dumps(request.target_languages),
                    state.engine,
                    request.correction_provider,
                    request.correction_model,
                    request.translation_provider,
                    request.translation_model,
                    request.context.model_dump_json(),
                    state.started_at.isoformat(),
                ),
            )

    def update_engine(self, session_id: str, engine: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET engine = ? WHERE session_id = ?",
                (engine, session_id),
            )

    def update_metadata(self, session_id: str, *, title: str, notes: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE sessions SET title = ?, notes = ? WHERE session_id = ?",
                (title, notes, session_id),
            )
        return cursor.rowcount > 0

    def mark_ended(self, session_id: str, ended_at: datetime | None = None) -> None:
        timestamp = (ended_at or datetime.now(UTC)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET ended_at = ?, interrupted = 0 WHERE session_id = ?",
                (timestamp, session_id),
            )

    def upsert_segment(self, session_id: str, event: TranscriptEvent) -> None:
        correction_json = (
            json.dumps(event.correction.model_dump(mode="json"), ensure_ascii=False)
            if event.correction is not None
            else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transcript_segments (
                    session_id, segment_id, version, stage, source_language,
                    text, translations_json, start_ms, end_ms, speaker,
                    confidence, correction_json, committed, emitted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, segment_id) DO UPDATE SET
                    version = excluded.version,
                    stage = excluded.stage,
                    source_language = excluded.source_language,
                    text = excluded.text,
                    translations_json = excluded.translations_json,
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    speaker = excluded.speaker,
                    confidence = excluded.confidence,
                    correction_json = excluded.correction_json,
                    committed = excluded.committed,
                    emitted_at = excluded.emitted_at
                WHERE excluded.version > transcript_segments.version
                """,
                (
                    session_id,
                    event.segment_id,
                    event.version,
                    event.stage.value,
                    event.source_language,
                    event.text,
                    json.dumps(event.translations, ensure_ascii=False),
                    event.start_ms,
                    event.end_ms,
                    event.speaker,
                    event.confidence,
                    correction_json,
                    int(event.committed),
                    event.emitted_at.isoformat(),
                ),
            )

    def list_sessions(self, limit: int = 50) -> list[SessionRecord]:
        safe_limit = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, COUNT(t.segment_id) AS segment_count
                FROM sessions s
                LEFT JOIN transcript_segments t ON t.session_id = s.session_id
                GROUP BY s.session_id
                ORDER BY s.started_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._session_record(row) for row in rows]

    def get_session(self, session_id: str) -> SessionDetail | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, COUNT(t.segment_id) AS segment_count
                FROM sessions s
                LEFT JOIN transcript_segments t ON t.session_id = s.session_id
                WHERE s.session_id = ?
                GROUP BY s.session_id
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        base = self._session_record(row)
        return SessionDetail(
            **base.model_dump(),
            context=SessionContext.model_validate_json(str(row["context_json"])),
        )

    def segments(self, session_id: str) -> list[TranscriptEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM transcript_segments
                WHERE session_id = ?
                ORDER BY start_ms, segment_id
                """,
                (session_id,),
            ).fetchall()
        return [self._segment(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            cursor = connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _session_record(row: sqlite3.Row) -> SessionRecord:
        ended_raw = row["ended_at"]
        return SessionRecord(
            session_id=str(row["session_id"]),
            join_code=str(row["join_code"]),
            title=str(row["title"]),
            notes=str(row["notes"]),
            presenter=str(row["presenter"]) if row["presenter"] is not None else None,
            preset=ProductPreset(str(row["preset"])),
            source_language=str(row["source_language"]),
            target_languages=json.loads(str(row["target_languages_json"])),
            engine=str(row["engine"]),
            correction_provider=str(row["correction_provider"]),
            correction_model=(
                str(row["correction_model"])
                if row["correction_model"] is not None
                else None
            ),
            translation_provider=str(row["translation_provider"]),
            translation_model=(
                str(row["translation_model"])
                if row["translation_model"] is not None
                else None
            ),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            ended_at=datetime.fromisoformat(str(ended_raw)) if ended_raw is not None else None,
            interrupted=bool(row["interrupted"]),
            segment_count=int(row["segment_count"]),
        )

    @staticmethod
    def _segment(row: sqlite3.Row) -> TranscriptEvent:
        correction_raw = row["correction_json"]
        correction = (
            CorrectionProvenance.model_validate(json.loads(str(correction_raw)))
            if correction_raw is not None
            else None
        )
        return TranscriptEvent(
            segment_id=str(row["segment_id"]),
            version=int(row["version"]),
            stage=str(row["stage"]),
            source_language=str(row["source_language"]),
            text=str(row["text"]),
            translations=json.loads(str(row["translations_json"])),
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]) if row["end_ms"] is not None else None,
            speaker=str(row["speaker"]) if row["speaker"] is not None else None,
            confidence=(
                float(row["confidence"])
                if row["confidence"] is not None
                else None
            ),
            correction=correction,
            committed=bool(row["committed"]),
            emitted_at=datetime.fromisoformat(str(row["emitted_at"])),
        )
