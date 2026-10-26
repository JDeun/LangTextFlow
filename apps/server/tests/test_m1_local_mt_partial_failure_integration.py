"""Integration checks for local MT target failure and terminal caption semantics."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.translation.base import TranslationError
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator


@pytest.mark.asyncio
async def test_one_failed_local_mt_target_does_not_erase_successful_caption() -> None:
    published: list[TranscriptEvent] = []
    finished = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            finished.set()

    async def prepare(self: M2M100CT2Translator) -> None:
        self._engine = object()
        self._tokenizer = object()

    def infer(
        self: M2M100CT2Translator,
        text: str, source: str, target: str, tokenizer: object, engine: object,
    ) -> str:
        del self, text, source, tokenizer, engine
        if target == "ja":
            raise TranslationError("model output rejected")
        return "A valid English translation"

    pipeline = CaptionPipeline(publish, Settings())
    with (
        patch.object(M2M100CT2Translator, "prepare", prepare),
        patch.object(M2M100CT2Translator, "_translate_sync", infer),
    ):
        await pipeline.start(StartSessionRequest(
            source_language="ko",
            target_languages=["en", "ja"],
            translation_provider="m2m100-ct2",
        ))
        try:
            await pipeline.ingest(TranscriptEvent(
                segment_id="local-mt-partial-failure",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ko",
                text="원본 자막은 반드시 유지합니다.",
                start_ms=100,
                end_ms=1500,
            ))
            await asyncio.wait_for(finished.wait(), timeout=3)
        finally:
            await pipeline.stop()

    assert [event.stage for event in published] == [
        CaptionStage.STABLE,
        CaptionStage.CORRECTED,
        CaptionStage.TRANSLATED,
        CaptionStage.TRANSLATED,
        CaptionStage.COMMITTED,
    ]
    terminal = published[-1]
    assert terminal.committed is True
    assert terminal.text == "원본 자막은 반드시 유지합니다."
    assert terminal.translations == {"en": "A valid English translation"}
    assert terminal.translation_states == {"en": "available", "ja": "failed"}
    assert terminal.start_ms == 100
    assert terminal.end_ms == 1500
    assert [event.version for event in published] == [1, 2, 3, 4, 5]
