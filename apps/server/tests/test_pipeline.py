import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.llm_correction import ConstrainedCorrector, CorrectionError
from langtextflow.models import (
    CaptionStage,
    ProductPreset,
    SessionContext,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.pipeline import CaptionPipeline


class FakeCorrector(ConstrainedCorrector):
    provider = "fake"
    model = "fake-corrector"

    def __init__(self, result: str | None = None, error: str | None = None) -> None:
        self.result = result
        self.error = error

    async def correct(
        self,
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        del source_language, context
        if self.error:
            raise CorrectionError(self.error)
        return self.result or text


class PipelineWithCorrector(CaptionPipeline):
    def __init__(self, publish, settings: Settings, corrector: ConstrainedCorrector) -> None:
        super().__init__(publish, settings)
        self.test_corrector = corrector

    def _build_corrector(self, request: StartSessionRequest) -> ConstrainedCorrector | None:
        del request
        return self.test_corrector


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


@pytest.mark.asyncio
async def test_pipeline_fans_out_any_source_to_multiple_targets() -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = CaptionPipeline(publish, Settings())
    await pipeline.start(
        StartSessionRequest(
            source_language="ja",
            target_languages=["ko", "en"],
            translation_provider="demo",
        )
    )
    try:
        await pipeline.ingest(
            TranscriptEvent(
                segment_id="seg-ja-1",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ja",
                text="福音について話します",
                start_ms=0,
                end_ms=1600,
            )
        )
        await asyncio.wait_for(committed.wait(), timeout=1.0)
    finally:
        await pipeline.stop()

    translated = next(event for event in published if event.stage is CaptionStage.TRANSLATED)
    assert translated.translations == {
        "ko": "[KO demo] 福音について話します",
        "en": "[EN demo] 福音について話します",
    }
    assert published[-1].translations == translated.translations
    assert published[-1].committed is True


@pytest.mark.asyncio
async def test_pipeline_applies_optional_llm_correction_after_deterministic_rules() -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = PipelineWithCorrector(
        publish,
        Settings(),
        FakeCorrector(result="오늘 요한복음 3장 말씀입니다."),
    )
    await pipeline.start(
        StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            correction_provider="fake",
            translation_provider="none",
            context=SessionContext(preset=ProductPreset.CHURCH),
        )
    )
    try:
        await pipeline.ingest(
            TranscriptEvent(
                segment_id="seg-correction",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ko",
                text="오늘 요한 보금 3장 말씀입니다",
                start_ms=0,
                end_ms=1200,
            )
        )
        await asyncio.wait_for(committed.wait(), timeout=1.0)
    finally:
        await pipeline.stop()

    corrected = next(event for event in published if event.stage is CaptionStage.CORRECTED)
    assert corrected.text == "오늘 요한복음 3장 말씀입니다."
    assert published[-1].text == corrected.text
    assert pipeline.correction_status.error is None


@pytest.mark.asyncio
async def test_pipeline_degrades_to_deterministic_result_when_llm_correction_fails() -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = PipelineWithCorrector(
        publish,
        Settings(),
        FakeCorrector(error="unsafe rewrite"),
    )
    await pipeline.start(
        StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            correction_provider="fake",
            translation_provider="none",
            context=SessionContext(preset=ProductPreset.CHURCH),
        )
    )
    try:
        await pipeline.ingest(
            TranscriptEvent(
                segment_id="seg-fallback",
                version=1,
                stage=CaptionStage.STABLE,
                source_language="ko",
                text="오늘 요한 보금 3장 말씀입니다",
                start_ms=0,
                end_ms=1200,
            )
        )
        await asyncio.wait_for(committed.wait(), timeout=1.0)
    finally:
        await pipeline.stop()

    corrected = next(event for event in published if event.stage is CaptionStage.CORRECTED)
    assert corrected.text == "오늘 요한복음 3장 말씀입니다"
    assert pipeline.correction_status.error is not None
    assert "deterministic result" in pipeline.correction_status.error
