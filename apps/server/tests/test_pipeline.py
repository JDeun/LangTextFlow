import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import (
    CaptionStage,
    ProductPreset,
    SessionContext,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.pipeline import CaptionPipeline


@pytest.mark.asyncio
async def test_stable_segment_flows_through_correction_translation_and_commit() -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = CaptionPipeline(publish, Settings())
    await pipeline.start(
        StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            translation_provider="demo",
            context=SessionContext(preset=ProductPreset.CHURCH),
        )
    )
    try:
        await pipeline.ingest(
            TranscriptEvent(
                segment_id="seg-1",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ko",
                text="오늘 우리가 볼 말씀은 요한 보금 삼장입니다",
                start_ms=0,
                end_ms=2000,
            )
        )
        await asyncio.wait_for(committed.wait(), timeout=1.0)
    finally:
        await pipeline.stop()

    assert [event.stage for event in published] == [
        CaptionStage.STABLE,
        CaptionStage.CORRECTED,
        CaptionStage.TRANSLATED,
        CaptionStage.COMMITTED,
    ]
    assert published[1].text == "오늘 우리가 볼 말씀은 요한복음 삼장입니다"
    assert published[2].translations["en"] == "Today we will look at John chapter 3."
    assert published[-1].committed is True
