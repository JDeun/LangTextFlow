"""Private, transactional glossary overrides bound to one event project."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import GlossaryEntry


class ProjectGlossaryInput(BaseModel):
    entries: list[GlossaryEntry] = Field(default_factory=list, max_length=500)


class ProjectGlossaryRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=5.0)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        # SessionRepository.initialize() must create the projects table first.
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_glossary_overrides (
                    project_id TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    term_key TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, source_language, term_key),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_project_glossary_project "
                "ON project_glossary_overrides(project_id)"
            )

    @staticmethod
    def _require_project(conn: sqlite3.Connection, project_id: str) -> None:
        if conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone() is None:
            raise KeyError("project not found")

    def list_for(
        self, project_id: str, source_language: str | None = None,
    ) -> list[GlossaryEntry]:
        with self._connect() as conn:
            self._require_project(conn, project_id)
            query = (
                "SELECT entry_json FROM project_glossary_overrides "
                "WHERE project_id = ?"
            )
            params: tuple[str, ...] = (project_id,)
            if source_language is not None:
                query += " AND source_language = ?"
                params += (source_language,)
            query += " ORDER BY source_language, term_key"
            return [
                GlossaryEntry.model_validate(json.loads(row["entry_json"]))
                for row in conn.execute(query, params).fetchall()
            ]

    def replace(self, project_id: str, entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
        if len(entries) > 500:
            raise ValueError("project glossary exceeds 500 entries")
        keys = [(row.source_language, row.term.casefold()) for row in entries]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate project glossary source-language term")
        stamp = datetime.now(UTC).isoformat()
        # Validate the entire submitted snapshot before deleting anything.
        normalized = [GlossaryEntry.model_validate(row.model_dump()) for row in entries]
        with self._connect() as conn:
            self._require_project(conn, project_id)
            conn.execute(
                "DELETE FROM project_glossary_overrides WHERE project_id = ?",
                (project_id,),
            )
            conn.executemany(
                """
                INSERT INTO project_glossary_overrides
                    (project_id, source_language, term_key, entry_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        project_id, row.source_language, row.term.casefold(),
                        row.model_dump_json(), stamp,
                    )
                    for row in normalized
                ],
            )
        return normalized
