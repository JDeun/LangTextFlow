"""Project-local glossary overlays; never mutate global terms for one event."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import GlossaryEntry, GlossaryRecord, ProductPreset


class ProjectGlossarySnapshot(BaseModel):
    entries: list[GlossaryEntry] = Field(default_factory=list, max_length=500)


class ProjectGlossaryRepository:
    """Normalized project scope with FK cascade and source-language unique keys."""

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_glossary_entries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    term_key TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id, source_language, term_key),
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                      ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_project_glossary_scope "
                "ON project_glossary_entries(project_id, source_language)"
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> GlossaryRecord:
        entry = GlossaryEntry.model_validate_json(str(row["entry_json"]))
        return GlossaryRecord(
            **entry.model_dump(),
            id=str(row["id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _validate_project(connection: sqlite3.Connection, project_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
        ).fetchone() is None:
            raise KeyError("project not found")

    def list(self, project_id: str) -> list[GlossaryRecord]:
        with self._connect() as connection:
            self._validate_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM project_glossary_entries WHERE project_id=? "
                "ORDER BY source_language, term_key",
                (project_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def active_overrides(
        self, project_id: str, preset: ProductPreset, source_language: str,
    ) -> list[GlossaryEntry]:
        # Include *disabled* records: a project may explicitly suppress a
        # global/preset term without deleting or modifying its source record.
        return [
            GlossaryEntry.model_validate(record.model_dump())
            for record in self.list(project_id)
            if record.source_language == source_language and record.applies_to(preset)
        ]

    def create(self, project_id: str, entry: GlossaryEntry) -> GlossaryRecord:
        now = datetime.now(UTC)
        record_id = str(uuid4())
        with self._connect() as connection:
            self._validate_project(connection, project_id)
            try:
                connection.execute(
                    "INSERT INTO project_glossary_entries "
                    "(id, project_id, source_language, term_key, entry_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record_id, project_id, entry.source_language,
                        entry.term.casefold(), entry.model_dump_json(),
                        now.isoformat(), now.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("duplicate project glossary term") from exc
        return GlossaryRecord(
            **entry.model_dump(), id=record_id, created_at=now, updated_at=now,
        )

    def update(
        self, project_id: str, record_id: str, entry: GlossaryEntry,
    ) -> GlossaryRecord | None:
        stamp = datetime.now(UTC)
        with self._connect() as connection:
            self._validate_project(connection, project_id)
            try:
                cursor = connection.execute(
                    "UPDATE project_glossary_entries SET "
                    "source_language=?, term_key=?, entry_json=?, updated_at=? "
                    "WHERE id=? AND project_id=?",
                    (
                        entry.source_language, entry.term.casefold(),
                        entry.model_dump_json(), stamp.isoformat(),
                        record_id, project_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("duplicate project glossary term") from exc
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM project_glossary_entries WHERE id=? AND project_id=?",
                (record_id, project_id),
            ).fetchone()
        return self._record(row)

    def delete(self, project_id: str, record_id: str) -> bool:
        with self._connect() as connection:
            self._validate_project(connection, project_id)
            cursor = connection.execute(
                "DELETE FROM project_glossary_entries WHERE project_id=? AND id=?",
                (project_id, record_id),
            )
        return cursor.rowcount > 0

    def replace_snapshot(
        self, project_id: str, entries: list[GlossaryEntry],
    ) -> list[GlossaryRecord]:
        """Atomically replace one project's entries without erasing global terms.

        Preserve existing IDs and created_at for matching source/term keys.
        Validate the complete payload before any destructive SQL statements.
        """
        if len(entries) > 500:
            raise ValueError("project glossary snapshot exceeds 500 entries")
        validated = [GlossaryEntry.model_validate(item.model_dump()) for item in entries]
        keys = [(row.source_language, row.term.casefold()) for row in validated]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate project glossary source-language term")
        stamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            self._validate_project(connection, project_id)
            previous = {
                (row["source_language"], row["term_key"]): (
                    str(row["id"]), str(row["created_at"])
                )
                for row in connection.execute(
                    "SELECT id, source_language, term_key, created_at "
                    "FROM project_glossary_entries WHERE project_id=?",
                    (project_id,),
                )
            }
            connection.execute(
                "DELETE FROM project_glossary_entries WHERE project_id=?", (project_id,),
            )
            for entry in validated:
                key = (entry.source_language, entry.term.casefold())
                record_id, created = previous.get(key, (str(uuid4()), stamp))
                connection.execute(
                    "INSERT INTO project_glossary_entries "
                    "(id, project_id, source_language, term_key, entry_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        record_id, project_id, key[0], key[1],
                        entry.model_dump_json(), created, stamp,
                    ),
                )
        return self.list(project_id)

    def import_entries(
        self, project_id: str, entries: list[GlossaryEntry], *,
        conflict_policy: str = "upsert",
    ) -> tuple[int, int, int]:
        if conflict_policy not in {"upsert", "skip"}:
            raise ValueError("conflict_policy must be upsert or skip")
        created = updated = skipped = 0
        with self._connect() as connection:
            self._validate_project(connection, project_id)
            existing = {
                (row["source_language"], row["term_key"]): row["id"]
                for row in connection.execute(
                    "SELECT id, source_language, term_key FROM project_glossary_entries "
                    "WHERE project_id=?", (project_id,),
                )
            }
            for entry in entries:
                key = (entry.source_language, entry.term.casefold())
                stamp = datetime.now(UTC).isoformat()
                if key in existing:
                    if conflict_policy == "skip":
                        skipped += 1
                        continue
                    connection.execute(
                        "UPDATE project_glossary_entries SET entry_json=?, "
                        "updated_at=? WHERE id=? AND project_id=?",
                        (entry.model_dump_json(), stamp, existing[key], project_id),
                    )
                    updated += 1
                else:
                    record_id = str(uuid4())
                    connection.execute(
                        "INSERT INTO project_glossary_entries "
                        "(id, project_id, source_language, term_key, entry_json, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            record_id, project_id, entry.source_language,
                            entry.term.casefold(), entry.model_dump_json(),
                            stamp, stamp,
                        ),
                    )
                    existing[key] = record_id
                    created += 1
        return created, updated, skipped
