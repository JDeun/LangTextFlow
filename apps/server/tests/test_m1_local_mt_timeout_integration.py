"""M1 timeout integration: a slow MT target cannot block source commit."""
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
async def test_local_mt_timeout_commits_source_without_waiting_for_native_cpu() -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    async def prepare(self: M2M100CT2Translator) -> None:
        self._engine = object()
        self._tokenizer = object()

    def infer(
        self: M2M100CT2Translator,
        text: str, source: str, target: str, tokenizer: object, engine: object,
    ) -> str:
        del self, text, source, target, tokenizer, engine
        time.sleep(0.2)
        return "delayed output"

    pipeline = CaptionPipeline(
        publish, Settings(translation_target_timeout_seconds=0.02),
    )
    with (
        patch.object(M2M100CT2Translator, "prepare", prepare),
        patch.object(M2M100CT2Translator, "_translate_sync", infer),
    ):
        await pipeline.start(StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            translation_provider="m2m100-ct2",
        ))
        try:
            await pipeline.ingest(TranscriptEvent(
                segment_id="slow-cpu",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ko",
                text="원본은 시간 초과에도 유지됩니다.",
                start_ms=0,
                end_ms=900,
            ))
            await asyncio.wait_for(committed.wait(), timeout=2)
        finally:
            await pipeline.stop()

    assert [event.stage for event in published] == [
        CaptionStage.STABLE, CaptionStage.CORRECTED,
        CaptionStage.TRANSLATED, CaptionStage.COMMITTED,
    ]
    terminal = published[-1]
    assert terminal.committed
    assert terminal.translation_states == {"en": "timed_out"}
    assert terminal.translations == {}
    assert terminal.text == "원본은 시간 초과에도 유지됩니다."
    assert [event.version for event in published] == [1, 2, 3, 4]
