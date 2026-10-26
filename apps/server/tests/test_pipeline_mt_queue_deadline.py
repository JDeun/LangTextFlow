"""Queued target languages must expire even when the CPU slot is occupied."""
from __future__ import annotations

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, SessionContext, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.translation.base import Translator


class SlowTranslator(Translator):
    async def prepare(self) -> None:
        pass

    async def translate(
        self, text: str, *, source_language: str, target_language: str,
        context: SessionContext,
    ) -> str:
        del text, source_language, target_language, context
        await asyncio.sleep(0.25)
        return "translated"

    async def close(self) -> None:
        pass


class SlowPipeline(CaptionPipeline):
    def _build_translator(self, request: StartSessionRequest) -> Translator:
        del request
        return SlowTranslator()


@pytest.mark.asyncio
async def test_target_timeout_covers_wait_for_translation_slot() -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = SlowPipeline(
        publish, Settings(translation_target_timeout_seconds=0.05),
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo", context=SessionContext(),
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="deadline", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="안녕하세요", start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(committed.wait(), timeout=0.5)
    finally:
        await pipeline.stop()
    terminal = next(event for event in published if event.stage is CaptionStage.COMMITTED)
    assert terminal.translation_states == {"en": "timed_out", "ja": "timed_out"}
    assert not terminal.translations
