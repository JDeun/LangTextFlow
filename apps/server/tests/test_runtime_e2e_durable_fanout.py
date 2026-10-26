"""Runtime integration: mock ASR -> correction -> MT -> live store -> SQLite."""
from __future__ import annotations

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_runtime_source_to_translated_commit_and_durable_history(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "captions.sqlite3")))
    state = await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ja",
            target_languages=["ko", "en"],
            translation_provider="demo",
        )
    )
    assert state.session_id is not None
    try:
        await runtime.pipeline.ingest(
            TranscriptEvent(
                segment_id="runtime-e2e-1",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ja",
                text="福音について話します",
                start_ms=0,
                end_ms=1500,
            )
        )
        async def committed() -> bool:
            return any(
                row.stage is CaptionStage.COMMITTED
                for row in runtime.store.snapshot()
            )

        for _ in range(100):
            if await committed():
                break
            await asyncio.sleep(0.01)
        assert await committed()
    finally:
        await runtime.stop()
    assert runtime.metrics.persistence_events_failed == 0
    assert runtime.metrics.persistence_events_dropped == 0
    stored = runtime.history.segments(state.session_id)
    assert len(stored) == 1
    final = stored[0]
    assert final.stage is CaptionStage.COMMITTED
    assert final.committed is True
    assert final.source_language == "ja"
    assert final.translations == {
        "ko": "[KO demo] 福音について話します",
        "en": "[EN demo] 福音について話します",
    }
    detail = runtime.history.get_session(state.session_id)
    assert detail is not None
    assert detail.ended_at is not None
    assert detail.interrupted is False
