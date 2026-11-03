"""Bounded operator-authored project sources, separate from spoken evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .post_event_archive import _now

MAX_ITEMS = 100
MAX_CHARACTERS = 120_000


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["brief", "handout", "agenda", "speaker", "decision", "todo"]
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=20_000)
    done: bool = False
    expected_revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def valid_text(self):
        if not self.title.strip() or not self.text.strip() or "\x00" in self.title + self.text:
            raise ValueError("Project text must be nonempty and contain no NUL characters")
        if self.done and self.kind != "todo":
            raise ValueError("Only operator-authored TODOs have a completion state")
        # Reject lone surrogates before persistence, hashing, or JSON transport.
        (self.title + self.text).encode("utf-8")
        return self


def content_sha(value):
    raw = json.dumps(
        {key: value[key] for key in ("kind", "title", "text", "done")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class KnowledgeConflict(ValueError):
    pass


class ProjectKnowledge:
    def __init__(self, archive):
        self.archive = archive

    def initialize(self):
        with self.archive._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS project_knowledge (
                    knowledge_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN
                        ('brief','handout','agenda','speaker','decision','todo')),
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    done INTEGER NOT NULL CHECK(done IN (0,1)),
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    content_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_knowledge ON project_knowledge(project_id);
            """)

    def listing(self, project_id, *, connection=None):
        if connection is None:
            with self.archive._connect() as conn:
                conn.execute("BEGIN")
                return self.listing(project_id, connection=conn)
        if not connection.execute(
            "SELECT 1 FROM projects WHERE project_id=?",
            (project_id,),
        ).fetchone():
            raise KeyError("project not found")
        rows = connection.execute(
            "SELECT * FROM project_knowledge WHERE project_id=? "
            "ORDER BY created_at,knowledge_id LIMIT ?",
            (project_id, MAX_ITEMS + 1),
        ).fetchall()
        if len(rows) > MAX_ITEMS or sum(len(r["text"]) for r in rows) > MAX_CHARACTERS:
            raise ValueError("Project materials exceed 100 items or 120000 text characters")
        return [{**dict(row), "done": bool(row["done"])} for row in rows]

    def save(self, project_id, payload, knowledge_id=None):
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            items = self.listing(project_id, connection=conn)
            old = next((r for r in items if r["knowledge_id"] == knowledge_id), None)
            if knowledge_id is not None and old is None:
                raise KeyError("project material not found")
            if (old is None and payload.expected_revision is not None) or (
                old is not None and payload.expected_revision != old["revision"]
            ):
                raise KnowledgeConflict("Project material changed; reload before saving")
            total = sum(len(r["text"]) for r in items if r != old) + len(payload.text)
            if (old is None and len(items) >= MAX_ITEMS) or total > MAX_CHARACTERS:
                raise ValueError("Project materials exceed 100 items or 120000 text characters")
            value = payload.model_dump(exclude={"expected_revision"})
            stamp = _now()
            result = {
                "knowledge_id": knowledge_id or str(uuid4()),
                "project_id": project_id,
                **value,
                "revision": old["revision"] + 1 if old else 1,
                "content_sha256": content_sha(value),
                "created_at": old["created_at"] if old else stamp,
                "updated_at": stamp,
            }
            conn.execute(
                "INSERT INTO project_knowledge VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(knowledge_id) DO UPDATE SET kind=excluded.kind,title=excluded.title,"
                "text=excluded.text,done=excluded.done,revision=excluded.revision,"
                "content_sha256=excluded.content_sha256,updated_at=excluded.updated_at",
                tuple(result.values()),
            )
            return result

    def delete(self, project_id, knowledge_id, expected_revision):
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = self.listing(project_id, connection=conn)
            old = next((r for r in rows if r["knowledge_id"] == knowledge_id), None)
            if old is None:
                raise KeyError("project material not found")
            if expected_revision != old["revision"]:
                raise KnowledgeConflict("Project material changed; reload before deleting")
            # Frozen input snapshots retain original notes. Explicit deletion must
            # remove those copies too, including approved historical digests.
            conn.execute(
                "DELETE FROM project_digests WHERE project_id=? AND EXISTS "
                "(SELECT 1 FROM json_each(snapshot_json,'$.knowledge') "
                "WHERE json_extract(value,'$.knowledge_id')=?)",
                (project_id, knowledge_id),
            )
            conn.execute(
                "DELETE FROM project_knowledge WHERE project_id=? AND knowledge_id=?",
                (project_id, knowledge_id),
            )
