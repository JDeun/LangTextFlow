"""Source revision admission is scoped to one running caption session."""

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore


@pytest.mark.asyncio
async def test_changed_stable_is_rejected_until_new_session_starts() -> None:
    store = CaptionStore()
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)
        if event.committed:
            committed.set()

    pipeline = CaptionPipeline(publish, Settings())
    request = StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="none",
    )

    def stable(text: str, version: int) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id="reused-id", version=version, stage=CaptionStage.STABLE,
            source_language="ko", text=text, start_ms=0, end_ms=500,
        )

    await pipeline.start(request)
    try:
        await pipeline.ingest(stable("첫 번째 원문", 1))
        await asyncio.wait_for(committed.wait(), timeout=1)
        await pipeline.ingest(stable("변경된 원문", 9))
        assert pipeline.revised_asr_events_rejected == 1
        assert store.snapshot()[0].text == "첫 번째 원문"
    finally:
        await pipeline.stop()

    store.clear()
    committed.clear()
    await pipeline.start(request)
    try:
        assert pipeline.revised_asr_events_rejected == 0
        await pipeline.ingest(stable("변경된 원문", 1))
        await asyncio.wait_for(committed.wait(), timeout=1)
        assert store.snapshot()[0].text == "변경된 원문"
        assert pipeline.duplicate_asr_events_dropped == 0
    finally:
        await pipeline.stop()
