"""Combined correction and MT failure must still produce terminal captions."""
from __future__ import annotations

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.llm_correction import ConstrainedCorrector, CorrectionError
from langtextflow.models import CaptionStage, SessionContext, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore


class FailingCorrector(ConstrainedCorrector):
    provider = "broken"
    model = "broken-correction"

    async def correct(
        self, text: str, *, source_language: str, context: SessionContext,
    ) -> str:
        raise CorrectionError("correction service offline")


class FailingTranslator:
    provider = "broken"
    model = "broken-mt"

    async def prepare(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def translate(
        self, text: str, *, source_language: str, target_language: str,
        context: SessionContext,
    ) -> str:
        raise RuntimeError("translation service offline")


class CombinedFailurePipeline(CaptionPipeline):
    def _build_corrector(self, request: StartSessionRequest):
        return FailingCorrector()

    def _build_translator(self, request: StartSessionRequest):
        return FailingTranslator()


@pytest.mark.asyncio
async def test_combined_correction_and_translation_failures_commit_source() -> None:
    store = CaptionStore()
    committed = asyncio.Event()
    events: list[TranscriptEvent] = []

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)
        events.append(event)
        if event.segment_id == "combined-failure" and event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = CombinedFailurePipeline(publish, Settings())
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        correction_provider="broken", translation_provider="broken",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="combined-failure", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="오늘 요한 보금 3장 말씀입니다",
            start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(committed.wait(), timeout=2)
    finally:
        await pipeline.stop()

    final = next(event for event in store.snapshot() if event.segment_id == "combined-failure")
    assert final.stage is CaptionStage.COMMITTED
    assert final.committed is True
    assert final.text == "오늘 요한복음 3장 말씀입니다"
    assert final.translations == {}
    assert final.translation_states == {"en": "failed", "ja": "failed"}
    assert final.correction is not None
    assert final.correction.method == "fallback"
    assert final.correction.llm_attempted is True
    assert final.correction.llm_applied is False
    assert pipeline.correction_status.error is not None
    assert pipeline.status.errors_by_language
