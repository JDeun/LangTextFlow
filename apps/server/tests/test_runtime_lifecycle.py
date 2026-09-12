import asyncio
import sqlite3

import pytest

from langtextflow.config import Settings
from langtextflow.models import StartSessionRequest
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_persistence_worker_exists_only_for_active_session(tmp_path) -> None:
    runtime = CaptionRuntime(
        Settings(database_path=str(tmp_path / "runtime.db"))
    )

    await runtime.initialize()
    assert runtime._persistence_task is None

    await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    assert runtime._persistence_task is not None

    await runtime.stop()
    assert runtime._persistence_task is None
    assert runtime.metrics.persistence_queue_depth == 0

    await runtime.shutdown()
    assert runtime._persistence_task is None


@pytest.mark.asyncio
async def test_persistence_disk_full_is_degraded_without_stopping_live_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    await runtime.initialize()

    def fail_upsert(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(runtime.history, "upsert_segment", fail_upsert)
    await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    try:
        for _ in range(100):
            if runtime.state.persistence_error:
                break
            await asyncio.sleep(0.01)

        assert runtime.state.running is True
        assert runtime.state.persistence_error is not None
        assert "disk is full" in runtime.state.persistence_error
        assert runtime.store.snapshot(), "live in-memory captions must survive persistence failure"
    finally:
        await runtime.stop()
        await runtime.shutdown()
