from __future__ import annotations

import io
import json
import os
import platform
import sqlite3
import sys
import threading
import traceback
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .models import SessionState
from .telemetry import RealtimeMetrics

_MAX_EVENT_LOG_BYTES = 1024 * 1024
_MAX_BUNDLE_EVENT_BYTES = 256 * 1024


class DiagnosticsStatus(BaseModel):
    previous_unclean_shutdown: bool = False
    recovered_session_count: int = 0
    recovered_session_ids: list[str] = Field(default_factory=list)
    database_integrity: str = "unknown"
    diagnostics_directory: str


class DiagnosticsManager:
    """Persist crash markers and build privacy-safe operator diagnostics bundles."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        database_path = Path(settings.database_path)
        self.directory = database_path.parent / "diagnostics"
        self.marker_path = self.directory / "runtime-state.json"
        self.events_path = self.directory / "events.jsonl"
        self.database_path = database_path
        self.previous_unclean_shutdown = False
        self.recovered_session_ids: list[str] = []
        self._lock = threading.Lock()
        self._started_at: datetime | None = None

    def initialize(self) -> DiagnosticsStatus:
        self.directory.mkdir(parents=True, exist_ok=True)
        previous = self._read_marker()
        self.previous_unclean_shutdown = bool(previous and not previous.get("clean_shutdown", False))
        self.recovered_session_ids = self._recover_unfinished_sessions()
        self._started_at = datetime.now(UTC)
        self._write_marker(clean_shutdown=False)
        self.record(
            "runtime_started",
            previous_unclean_shutdown=self.previous_unclean_shutdown,
            recovered_session_count=len(self.recovered_session_ids),
        )
        return self.status()

    def mark_clean_shutdown(self) -> None:
        self.record("runtime_clean_shutdown")
        self._write_marker(clean_shutdown=True, ended_at=datetime.now(UTC))

    def record(self, event: str, **fields: Any) -> None:
        safe_fields = {
            key: value
            for key, value in fields.items()
            if key not in {"join_code", "api_key", "text", "transcript", "reference_text"}
        }
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event[:120],
            **safe_fields,
        }
        encoded = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with self._lock:
            self._rotate_events_if_needed(len(encoded))
            with self.events_path.open("ab") as handle:
                handle.write(encoded)

    def record_exception(self, event: str, exc: BaseException) -> None:
        frames = traceback.extract_tb(exc.__traceback__)[-8:] if exc.__traceback__ else []
        self.record(
            event,
            exception_type=type(exc).__name__,
            stack=[f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames],
        )

    def status(self) -> DiagnosticsStatus:
        return DiagnosticsStatus(
            previous_unclean_shutdown=self.previous_unclean_shutdown,
            recovered_session_count=len(self.recovered_session_ids),
            recovered_session_ids=list(self.recovered_session_ids),
            database_integrity=self.database_integrity(),
            diagnostics_directory=str(self.directory),
        )

    def database_integrity(self) -> str:
        if not self.database_path.exists():
            return "missing"
        try:
            with sqlite3.connect(self.database_path, timeout=5.0) as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error:
            return "error"
        if row is None:
            return "unknown"
        return str(row[0])

    def build_bundle(self, state: SessionState, metrics: RealtimeMetrics) -> bytes:
        status = self.status()
        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "app": {
                "name": self.settings.app_name,
                "version": __version__,
                "environment": self.settings.environment,
            },
            "system": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": sys.version.split()[0],
            },
            "recovery": {
                "previous_unclean_shutdown": status.previous_unclean_shutdown,
                "recovered_session_count": status.recovered_session_count,
                "database_integrity": status.database_integrity,
            },
            "runtime": {
                "running": state.running,
                "engine": state.engine,
                "source_language": state.source_language,
                "target_languages": state.target_languages,
                "audio_required": state.audio_required,
                "correction_provider": state.correction_status.provider,
                "correction_available": state.correction_status.available,
                "translation_provider": state.translation_status.provider,
                "translation_available": state.translation_status.available,
                "persistence_error_type": (
                    "present" if state.persistence_error else None
                ),
            },
            "metrics": metrics.model_dump(mode="json"),
        }

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "diagnostics.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
            recent_events = self._recent_events()
            if recent_events:
                archive.writestr("events.jsonl", recent_events)
        return buffer.getvalue()

    def _recover_unfinished_sessions(self) -> list[str]:
        if not self.database_path.exists():
            return []
        recovered_at = datetime.now(UTC).isoformat()
        try:
            with sqlite3.connect(self.database_path, timeout=5.0) as connection:
                rows = connection.execute(
                    "SELECT session_id FROM sessions WHERE ended_at IS NULL"
                ).fetchall()
                session_ids = [str(row[0]) for row in rows]
                if session_ids:
                    connection.execute(
                        "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
                        (recovered_at,),
                    )
                return session_ids
        except sqlite3.Error:
            return []

    def _read_marker(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return raw if isinstance(raw, dict) else None

    def _write_marker(
        self,
        *,
        clean_shutdown: bool,
        ended_at: datetime | None = None,
    ) -> None:
        payload = {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": (
                self._started_at.isoformat() if self._started_at is not None else None
            ),
            "ended_at": ended_at.isoformat() if ended_at is not None else None,
            "clean_shutdown": clean_shutdown,
            "previous_unclean_shutdown": self.previous_unclean_shutdown,
            "recovered_session_count": len(self.recovered_session_ids),
        }
        temp_path = self.marker_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(self.marker_path)

    def _rotate_events_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_size = self.events_path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size + incoming_bytes <= _MAX_EVENT_LOG_BYTES:
            return
        rotated = self.events_path.with_suffix(".jsonl.1")
        try:
            rotated.unlink()
        except FileNotFoundError:
            pass
        try:
            self.events_path.replace(rotated)
        except FileNotFoundError:
            pass

    def _recent_events(self) -> bytes:
        try:
            with self.events_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - _MAX_BUNDLE_EVENT_BYTES))
                data = handle.read()
        except FileNotFoundError:
            return b""
        if len(data) == size:
            return data
        newline = data.find(b"\n")
        return data[newline + 1 :] if newline >= 0 else b""
