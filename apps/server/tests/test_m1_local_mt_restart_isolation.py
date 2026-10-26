"""Session restarts must not publish stale local-MT results into a new session."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator


@pytest.mark.asyncio
async def test_restart_cancels_old_mt_waiter_and_new_session_commits() -> None:
    published: list[TranscriptEvent] = []
    new_committed = asyncio.Event()
    native_started = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.segment_id == "new" and event.stage is CaptionStage.COMMITTED:
            new_committed.set()

    async def prepare(self: M2M100CT2Translator) -> None:
        self._engine = object()
        self._tokenizer = object()

    def infer(
        self: M2M100CT2Translator,
        text: str, source: str, target: str, tokenizer: object, engine: object,
    ) -> str:
        del self, source, target, tokenizer, engine
        if text == "old caption":
            loop.call_soon_threadsafe(native_started.set)
            time.sleep(0.2)
        return f"translated {text}"

    loop = asyncio.get_running_loop()
    pipeline = CaptionPipeline(
        publish,
        Settings(postprocess_drain_timeout_seconds=0.01),
    )
    request = StartSessionRequest(
        source_language="ko",
        target_languages=["en"],
        translation_provider="m2m100-ct2",
    )
    with (
        patch.object(M2M100CT2Translator, "prepare", prepare),
        patch.object(M2M100CT2Translator, "_translate_sync", infer),
    ):
        await pipeline.start(request)
        try:
            await pipeline.ingest(TranscriptEvent(
                segment_id="old", version=1, stage=CaptionStage.STABLE,
                source_language="ko", text="old caption", start_ms=0, end_ms=1000,
            ))
            await asyncio.wait_for(native_started.wait(), timeout=2)
            await pipeline.start(request)
            old_event_count = sum(event.segment_id == "old" for event in published)
            await pipeline.ingest(TranscriptEvent(
                segment_id="new", version=1, stage=CaptionStage.STABLE,
                source_language="ko", text="new caption", start_ms=0, end_ms=1000,
            ))
            await asyncio.wait_for(new_committed.wait(), timeout=3)
            await asyncio.sleep(0.25)
        finally:
            await pipeline.stop()

    assert sum(event.segment_id == "old" for event in published) == old_event_count
    new_events = [event for event in published if event.segment_id == "new"]
    assert new_events[-1].stage is CaptionStage.COMMITTED
    assert new_events[-1].translations == {"en": "translated new caption"}
    assert new_events[-1].translation_states == {"en": "available"}
    assert [event.version for event in new_events] == [1, 2, 3, 4]
