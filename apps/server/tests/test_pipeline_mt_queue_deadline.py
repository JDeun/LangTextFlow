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


class RecordingTranslator(Translator):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, SessionContext]] = []

    async def prepare(self) -> None:
        pass

    async def translate(
        self, text: str, *, source_language: str, target_language: str,
        context: SessionContext,
    ) -> str:
        del text
        self.calls.append((source_language, target_language, context))
        return "translated"

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_inflight_target_uses_original_session_language_and_context() -> None:
    translated = RecordingTranslator()
    published: list[TranscriptEvent] = []
    corrected_seen = asyncio.Event()
    release_correction = asyncio.Event()
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.CORRECTED:
            corrected_seen.set()
            await release_correction.wait()
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    class RecordingPipeline(CaptionPipeline):
        def _build_translator(self, request: StartSessionRequest) -> Translator:
            del request
            return translated

    pipeline = RecordingPipeline(publish, Settings())
    original_context = SessionContext()
    original = StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="demo", context=original_context,
    )
    await pipeline.start(original)
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="session-snapshot", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="안녕하세요", start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(corrected_seen.wait(), timeout=1.0)
        # Simulate a racing session replacement at the corrected publish boundary.
        pipeline.request = StartSessionRequest(
            source_language="ja", target_languages=["ko"],
            translation_provider="demo", context=SessionContext(),
        )
        release_correction.set()
        await asyncio.wait_for(committed.wait(), timeout=1.0)
    finally:
        release_correction.set()
        await pipeline.stop()
    assert translated.calls == [("ko", "en", original_context)]
    assert published[-1].translation_states == {"en": "available"}
