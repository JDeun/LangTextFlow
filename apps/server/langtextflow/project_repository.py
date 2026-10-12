"""Event projects group independent caption sessions without owning their data."""
from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ProjectInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=8000)
    event_date: date | None = None
    location: str = Field(default="", max_length=240)
    status: str = Field(default="planning", pattern=r"^(planning|active|completed|archived)$")

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project title cannot be blank")
        return value


class ProjectRecord(ProjectInput):
    project_id: str
    created_at: datetime
    updated_at: datetime
    session_count: int = 0


class ProjectAssignment(BaseModel):
    project_id: str | None = Field(default=None, max_length=64)


class ProjectRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _project(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=row["project_id"], title=row["title"],
            description=row["description"], event_date=row["event_date"],
            location=row["location"], status=row["status"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            session_count=row["session_count"],
        )

    def create(self, payload: ProjectInput) -> ProjectRecord:
        project_id = str(uuid4())
        stamp = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects
                  (project_id, title, description, event_date, location, status,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, payload.title, payload.description,
                    payload.event_date.isoformat() if payload.event_date else None,
                    payload.location, payload.status, stamp, stamp,
                ),
            )
        result = self.get(project_id)
        assert result is not None
        return result

    def list(self, limit: int = 100) -> list[ProjectRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, COUNT(ps.session_id) AS session_count
                FROM projects AS p
                LEFT JOIN project_sessions AS ps ON ps.project_id=p.project_id
                GROUP BY p.project_id
                ORDER BY COALESCE(p.event_date, SUBSTR(p.created_at, 1, 10)) DESC,
                         p.created_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [self._project(row) for row in rows]

    def get(self, project_id: str) -> ProjectRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, COUNT(ps.session_id) AS session_count
                FROM projects AS p
                LEFT JOIN project_sessions AS ps ON ps.project_id=p.project_id
                WHERE p.project_id=?
                GROUP BY p.project_id
                """,
                (project_id,),
            ).fetchone()
        return self._project(row) if row is not None else None

    def update(self, project_id: str, payload: ProjectInput) -> ProjectRecord | None:
        stamp = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE projects SET
                  title=?, description=?, event_date=?, location=?, status=?, updated_at=?
                WHERE project_id=?
                """,
                (
                    payload.title, payload.description,
                    payload.event_date.isoformat() if payload.event_date else None,
                    payload.location, payload.status, stamp, project_id,
                ),
            )
            if result.rowcount == 0:
                return None
        return self.get(project_id)

    def delete(self, project_id: str) -> bool:
        """Detach sessions through FK cascade; their transcripts remain intact."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM projects WHERE project_id=?", (project_id,),
            )
        return cursor.rowcount > 0

    def assign(self, session_id: str, project_id: str | None) -> None:
        with self._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,),
            ).fetchone() is None:
                raise KeyError("session not found")
            if project_id is None:
                conn.execute(
                    "DELETE FROM project_sessions WHERE session_id=?", (session_id,),
                )
                return
            if conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (project_id,),
            ).fetchone() is None:
                raise KeyError("project not found")
            conn.execute(
                """
                INSERT INTO project_sessions (session_id, project_id)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id
                """,
                (session_id, project_id),
            )
