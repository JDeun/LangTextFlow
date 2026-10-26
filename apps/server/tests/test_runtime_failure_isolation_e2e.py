"""Regression tests for independent realtime and durable caption outputs."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


def _stable(segment_id: str) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id=segment_id,
        version=1,
        stage=CaptionStage.STABLE,
        source_language="ko",
        text="오늘 말씀입니다",
        start_ms=0,
        end_ms=1000,
    )


@pytest.mark.asyncio
async def test_sqlite_write_failure_does_not_block_live_caption(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "failure.sqlite3")))
    await runtime.start(
        StartSessionRequest(
            engine="mock", source_language="ko", target_languages=["en"],
            translation_provider="demo",
        )
    )
    try:
        with patch.object(runtime.history, "upsert_segment", side_effect=OSError("disk full")):
            await runtime.pipeline.ingest(_stable("disk-failure"))
            for _ in range(100):
                if any(
                    event.stage is CaptionStage.COMMITTED
                    for event in runtime.store.snapshot()
                ) and runtime.metrics.persistence_events_failed:
                    break
                await asyncio.sleep(0.01)
            assert next(\n                event for event in runtime.store.snapshot()\n                if event.segment_id == "disk-failure"\n            ).stage is CaptionStage.COMMITTED
            assert runtime.metrics.persistence_events_failed > 0
            assert runtime.state.persistence_error is not None
    finally:
        await runtime.stop()

