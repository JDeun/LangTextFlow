from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import GlossaryEntry, GlossaryRecord, ProductPreset


class GlossaryRepository:
    """SQLite-backed glossary storage kept independent from realtime caption state."""

    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS glossary_entries (
                    id TEXT PRIMARY KEY,
                    term TEXT NOT NULL,
                    source_language TEXT NOT NULL DEFAULT 'ko',
                    aliases_json TEXT NOT NULL,
                    translations_json TEXT NOT NULL,
                    category TEXT NOT NULL,
                    presets_json TEXT NOT NULL DEFAULT '[]',
                    boost REAL NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(glossary_entries)").fetchall()
            }
            if "presets_json" not in columns:
                connection.execute(
                    "ALTER TABLE glossary_entries ADD COLUMN presets_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            if "source_language" not in columns:
                # Existing records had no source code. Retain their Korean-source
                # behavior; operators must review imported non-Korean terms.
                connection.execute(
                    "ALTER TABLE glossary_entries ADD COLUMN source_language "
                    "TEXT NOT NULL DEFAULT 'ko'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_glossary_term ON glossary_entries(term)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_glossary_source_term "
                "ON glossary_entries(source_language, term)"
            )

    def list(self) -> list[GlossaryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM glossary_entries ORDER BY category, term"
            ).fetchall()
        return [self._record(row) for row in rows]

    def active_for(
        self, preset: ProductPreset, source_language: str = "ko"
    ) -> list[GlossaryEntry]:
        entries: list[GlossaryEntry] = []
        for record in self.list():
            if (record.enabled and record.applies_to(preset)
                    and record.source_language == source_language):
                entries.append(
                    GlossaryEntry(
                        term=record.term,
                        source_language=record.source_language,
                        aliases=record.aliases,
                        translations=record.translations,
                        category=record.category,
                        presets=record.presets,
                        boost=record.boost,
                        enabled=True,
                    )
                )
        return entries

    def get(self, record_id: str) -> GlossaryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM glossary_entries WHERE id = ?",
                (record_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def create(self, entry: GlossaryEntry) -> GlossaryRecord:
        now = datetime.now(UTC)
        record = GlossaryRecord(
            **entry.model_dump(include=set(GlossaryEntry.model_fields)),
            id=str(uuid4()),
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            self._assert_source_term_free(connection, record)
            connection.execute(
                """
                INSERT INTO glossary_entries (
                    id, term, source_language, aliases_json, translations_json, category,
                    presets_json, boost, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._params(record),
            )
        return record

    def update(self, record_id: str, entry: GlossaryEntry) -> GlossaryRecord | None:
        current = self.get(record_id)
        if current is None:
            return None
        record = GlossaryRecord(
            **entry.model_dump(include=set(GlossaryEntry.model_fields)),
            id=record_id,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
        )
        with self._connect() as connection:
            self._assert_source_term_free(connection, record, excluded_id=record_id)
            self._update_record(connection, record)
        return record

    def delete(self, record_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM glossary_entries WHERE id = ?",
                (record_id,),
            )
        return cursor.rowcount > 0

    def seed(self, entries: list[GlossaryEntry]) -> list[GlossaryRecord]:
        existing_terms = {
            (record.source_language, record.term.casefold()) for record in self.list()
        }
        for entry in entries:
            key = (entry.source_language, entry.term.casefold())
            if key not in existing_terms:
                self.create(entry)
                existing_terms.add(key)
        return self.list()

    def import_entries(
        self,
        entries: list[GlossaryEntry],
        *,
        conflict_policy: str = "upsert",
    ) -> tuple[int, int, int]:
        if conflict_policy not in {"upsert", "skip"}:
            raise ValueError("conflict_policy must be upsert or skip")

        created = 0
        updated = 0
        skipped = 0
        with self._connect() as connection:
            existing_rows = connection.execute("SELECT * FROM glossary_entries").fetchall()
            existing: dict[tuple[str, str], GlossaryRecord] = {}
            for row in existing_rows:
                record = self._record(row)
                existing.setdefault((record.source_language, record.term.casefold()), record)

            for entry in entries:
                key = (entry.source_language, entry.term.casefold())
                current = existing.get(key)
                now = datetime.now(UTC)
                if current is not None:
                    if conflict_policy == "skip":
                        skipped += 1
                        continue
                    record = GlossaryRecord(
                        **entry.model_dump(include=set(GlossaryEntry.model_fields)),
                        id=current.id,
                        created_at=current.created_at,
                        updated_at=now,
                    )
                    self._update_record(connection, record)
                    existing[key] = record
                    updated += 1
                    continue

                record = GlossaryRecord(
                    **entry.model_dump(include=set(GlossaryEntry.model_fields)),
                    id=str(uuid4()),
                    created_at=now,
                    updated_at=now,
                )
                connection.execute(
                    """
                    INSERT INTO glossary_entries (
                        id, term, source_language, aliases_json, translations_json, category,
                        presets_json, boost, enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._params(record),
                )
                existing[key] = record
                created += 1

        return created, updated, skipped

    @staticmethod
    def _assert_source_term_free(
        connection: sqlite3.Connection,
        entry: GlossaryRecord,
        excluded_id: str | None = None,
    ) -> None:
        rows = connection.execute(
            "SELECT id, term FROM glossary_entries WHERE source_language = ?",
            (entry.source_language,),
        ).fetchall()
        for row in rows:
            if row["id"] != excluded_id and str(row["term"]).casefold() == entry.term.casefold():
                raise ValueError(
                    f"duplicate glossary term for {entry.source_language}: {entry.term}"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _update_record(connection: sqlite3.Connection, record: GlossaryRecord) -> None:
        connection.execute(
            """
            UPDATE glossary_entries
            SET term = ?, source_language = ?, aliases_json = ?,
                translations_json = ?, category = ?, presets_json = ?,
                boost = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                record.term,
                record.source_language,
                json.dumps(record.aliases, ensure_ascii=False),
                json.dumps(record.translations, ensure_ascii=False),
                record.category,
                json.dumps([preset.value for preset in record.presets]),
                record.boost,
                int(record.enabled),
                record.updated_at.isoformat(),
                record.id,
            ),
        )

    @staticmethod
    def _params(record: GlossaryRecord) -> tuple[object, ...]:
        return (
            record.id,
            record.term,
            record.source_language,
            json.dumps(record.aliases, ensure_ascii=False),
            json.dumps(record.translations, ensure_ascii=False),
            record.category,
            json.dumps([preset.value for preset in record.presets]),
            record.boost,
            int(record.enabled),
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> GlossaryRecord:
        keys = set(row.keys())
        presets = json.loads(str(row["presets_json"])) if "presets_json" in keys else []
        return GlossaryRecord(
            id=str(row["id"]),
            term=str(row["term"]),
            source_language=(str(row["source_language"]) if "source_language" in keys
                             else "ko"),
            aliases=json.loads(str(row["aliases_json"])),
            translations=json.loads(str(row["translations_json"])),
            category=str(row["category"]),
            presets=[ProductPreset(value) for value in presets],
            boost=float(row["boost"]),
            enabled=bool(row["enabled"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
