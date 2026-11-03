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


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["correction", "publication"])
async def test_target_keeps_originating_session_and_provider(boundary, monkeypatch) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    committed = asyncio.Event()
    calls = []

    class RecordingTranslator(SlowTranslator):
        async def translate(self, text, *, source_language, target_language, context):
            calls.append((source_language, target_language, context))
            return "translated"

    async def publish(event):
        if boundary == "publication" and event.stage is CaptionStage.CORRECTED:
            entered.set()
            await release.wait()
        if event.committed:
            committed.set()

    pipeline = CaptionPipeline(publish, Settings())
    translator = RecordingTranslator()
    monkeypatch.setattr(pipeline, "_build_translator", lambda request: translator)
    original = StartSessionRequest(
        source_language="en", target_languages=["ko", "ja"], translation_provider="demo",
    )
    await pipeline.start(original)
    correct = pipeline._correct

    async def paused_correct(text):
        result = await correct(text)
        if boundary == "correction":
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(pipeline, "_correct", paused_correct)
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="origin", version=1, stage=CaptionStage.STABLE,
            source_language="en", text="Grace", start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(entered.wait(), 1)
        pipeline.request = StartSessionRequest(
            source_language="ja", target_languages=["en"], translation_provider="none",
        )
        pipeline.translator = None
        release.set()
        await asyncio.wait_for(committed.wait(), 1)
        assert sorted((source, target) for source, target, _ in calls) == [
            ("en", "ja"), ("en", "ko"),
        ]
        assert all(context is original.context for _, _, context in calls)
    finally:
        release.set()
        pipeline.translator = translator
        await pipeline.stop()
