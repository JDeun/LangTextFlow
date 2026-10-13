"""Inject SQLite persistence failures without a database, disk or model.

A caption stream may continue after an archive-write error, but its operator
status must never expose internal SQLite strings, local paths or credentials.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


def _event(version: int) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id=f"section-{version}",
        version=1,
        stage=CaptionStage.COMMITTED,
        committed=True,
        source_language="ko",
        text="실제 발화 내용",
        start_ms=1000 * version,
        end_ms=1000 * version + 500,
    )


@pytest.mark.asyncio
async def test_sqlite_failure_redacts_db_path_and_preserves_queue_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "history.db")))
    runtime.state.session_id = "test-session"
    persisted: list[str] = []
    secret = "file:///private/customer/tenant-secret.sqlite?auth=supersecret"

    def write(_session_id: str, event: TranscriptEvent) -> None:
        if event.segment_id == "section-1":
            raise sqlite3.OperationalError(f"database is locked: {secret}")
        persisted.append(event.segment_id)

    monkeypatch.setattr(runtime.history, "upsert_segment", write)
    runtime._start_persistence_worker()
    try:
        runtime._persistence_queue.put_nowait(("test-session", _event(1)))
        runtime._persistence_queue.put_nowait(("test-session", _event(2)))
        await asyncio.wait_for(runtime._persistence_queue.join(), timeout=2.0)
        assert persisted == ["section-2"]
        assert runtime._persistence_task is not None
        assert not runtime._persistence_task.done()
        error = runtime.state.persistence_error
        assert error is not None
        assert "storage unavailable" in error
        assert "tenant-secret" not in error
        assert "supersecret" not in error
        assert "private" not in error
    finally:
        await asyncio.wait_for(runtime._stop_persistence_worker(), timeout=2.0)
    assert runtime._persistence_task is None


@pytest.mark.asyncio
async def test_runtime_engine_metadata_write_does_not_expose_raw_sqlite_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "history.db")))
    runtime.state.session_id = "test-session"
    raw = "disk I/O error at /very/sensitive/path; token=secret"

    def broken_update(_session_id: str, _engine: str) -> None:
        raise sqlite3.OperationalError(raw)

    monkeypatch.setattr(runtime.history, "update_engine", broken_update)
    await runtime._provider_changed("mock")
    assert runtime.state.engine == "mock"
    assert runtime.state.persistence_error is not None
    assert "storage unavailable" in runtime.state.persistence_error
    assert raw not in runtime.state.persistence_error


@pytest.mark.asyncio
async def test_worker_flushes_queue_on_multiple_failures_without_deadlock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "history.db")))
    runtime.state.session_id = "test-session"

    def broken_write(_session_id: str, _event: TranscriptEvent) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(runtime.history, "upsert_segment", broken_write)
    runtime._start_persistence_worker()
    try:
        for n in range(1, 8):
            runtime._persistence_queue.put_nowait(("test-session", _event(n)))
        await asyncio.wait_for(runtime._persistence_queue.join(), timeout=2.0)
        assert runtime.state.persistence_error is not None
    finally:
        await asyncio.wait_for(runtime._stop_persistence_worker(), timeout=2.0)
    assert runtime._persistence_queue.empty()
