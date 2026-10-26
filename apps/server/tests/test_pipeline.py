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
    assert published[1].correction is not None
    assert published[1].correction.method == "deterministic"
    assert published[1].correction.deterministic_changed is True
    assert published[1].correction.changed is True
    assert published[2].translations["en"] == "Today we will look at John chapter 3."
    assert published[-1].correction == published[1].correction
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

    translated = next(
        event for event in reversed(published)
        if event.stage is CaptionStage.TRANSLATED
    )
    assert translated.translations == {
        "ko": "[KO demo] 福音について話します",
        "en": "[EN demo] 福音について話します",
    }
    assert published[-1].translations == translated.translations
    assert published[-1].committed is True


@pytest.mark.asyncio
async def test_pipeline_records_llm_correction_provenance() -> None:
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
    assert corrected.correction is not None
    assert corrected.correction.method == "llm"
    assert corrected.correction.provider == "fake"
    assert corrected.correction.model == "fake-corrector"
    assert corrected.correction.deterministic_changed is True
    assert corrected.correction.llm_attempted is True
    assert corrected.correction.llm_applied is True
    assert corrected.correction.changed is True
    assert corrected.correction.fallback_reason is None
    assert published[-1].correction == corrected.correction
    assert pipeline.correction_status.error is None


@pytest.mark.asyncio
async def test_pipeline_records_fallback_provenance_when_llm_correction_fails() -> None:
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
    assert corrected.correction is not None
    assert corrected.correction.method == "fallback"
    assert corrected.correction.provider == "fake"
    assert corrected.correction.llm_attempted is True
    assert corrected.correction.llm_applied is False
    assert corrected.correction.deterministic_changed is True
    assert corrected.correction.fallback_reason is not None
    assert "unsafe rewrite" in corrected.correction.fallback_reason
    assert pipeline.correction_status.error is not None
    assert "deterministic result" in pipeline.correction_status.error


@pytest.mark.asyncio
async def test_pipeline_stop_drains_ready_postprocessing() -> None:
    published: list[TranscriptEvent] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    pipeline = CaptionPipeline(publish, Settings(postprocess_drain_timeout_seconds=0.5))
    await pipeline.start(
        StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            translation_provider="demo",
        )
    )
    await pipeline.ingest(
        TranscriptEvent(
            segment_id="drain",
            version=1,
            stage=CaptionStage.STABLE,
            source_language="ko",
            text="graceful drain",
            start_ms=0,
            end_ms=100,
        )
    )
    await pipeline.stop()

    assert published[-1].stage is CaptionStage.COMMITTED
    assert published[-1].committed is True


class BlockingCorrector(ConstrainedCorrector):
    provider = "blocking"
    model = "blocking-corrector"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.never = asyncio.Event()

    async def correct(
        self,
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        del text, source_language, context
        self.started.set()
        await self.never.wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_pipeline_stop_cancels_blocked_postprocessing() -> None:
    published: list[TranscriptEvent] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    corrector = BlockingCorrector()
    pipeline = PipelineWithCorrector(
        publish,
        Settings(postprocess_drain_timeout_seconds=0.05),
        corrector,
    )
    await pipeline.start(
        StartSessionRequest(
            source_language="ko",
            target_languages=["en"],
            correction_provider="fake",
            translation_provider="none",
        )
    )
    await pipeline.ingest(
        TranscriptEvent(
            segment_id="blocked",
            version=1,
            stage=CaptionStage.STABLE,
            source_language="ko",
            text="blocked correction",
            start_ms=0,
            end_ms=100,
        )
    )
    await asyncio.wait_for(corrector.started.wait(), timeout=1.0)
    await asyncio.wait_for(pipeline.stop(), timeout=0.2)

    assert pipeline._worker_task is None
    assert pipeline.queue_depth == 0
    assert [event.stage for event in published] == [
        CaptionStage.STABLE, CaptionStage.COMMITTED,
    ]
    assert published[-1].translation_states == {"en": "unsupported"}
    assert published[-1].committed is True


class ControllableTranslator:
    """Deterministic independent-target fixture; no model downloads."""
    provider = "controlled"
    model = "fake"

    def __init__(self, *, fail: str | None = None, stall: str | None = None) -> None:
        self.fail = fail
        self.stall = stall
        self.started: set[str] = set()
        self.both_started = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def prepare(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def translate(
        self, text: str, *, source_language: str, target_language: str,
        context: SessionContext,
    ) -> str:
        del source_language, context
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            # Simulate an awaited translation call. An immediate-return fake
            # cannot demonstrate overlapping language inference even when
            # the runtime correctly schedules concurrent target tasks.
            await asyncio.sleep(0)
            self.started.add(target_language)
            if len(self.started) == 2:
                self.both_started.set()
            if self.stall == target_language:
                await asyncio.Event().wait()
            if self.fail == target_language:
                raise RuntimeError("injected provider failure with private detail")
            return f"{target_language}: {text}"
        finally:
            self.active -= 1


class PipelineWithTranslator(CaptionPipeline):
    def __init__(self, publish, settings: Settings, translator: ControllableTranslator):
        super().__init__(publish, settings)
        self.test_translator = translator

    def _build_translator(self, request: StartSessionRequest):
        del request
        return self.test_translator


@pytest.mark.asyncio
async def test_parallel_target_translations_start_before_either_finishes() -> None:
    events: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    translator = ControllableTranslator()
    pipeline = PipelineWithTranslator(publish, Settings(), translator)
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="one", version=1, stage=CaptionStage.STABLE,
            text="안녕하세요", source_language="ko", start_ms=0, end_ms=100,
        ))
        await asyncio.wait_for(committed.wait(), timeout=1)
    finally:
        await pipeline.stop()

    assert translator.max_active == 2
    assert events[-1].translations == {"en": "en: 안녕하세요", "ja": "ja: 안녕하세요"}
    assert pipeline.status.errors_by_language == {}


@pytest.mark.asyncio
async def test_target_exception_does_not_erase_other_language_output() -> None:
    events: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = PipelineWithTranslator(
        publish, Settings(), ControllableTranslator(fail="en"),
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="failure", version=1, stage=CaptionStage.STABLE,
            text="성령", source_language="ko", start_ms=0, end_ms=100,
        ))
        await asyncio.wait_for(committed.wait(), timeout=1)
    finally:
        await pipeline.stop()

    assert events[0].stage is CaptionStage.STABLE
    assert events[-1].committed is True
    assert events[-1].translations == {"ja": "ja: 성령"}
    assert pipeline.status.errors_by_language == {
        "en": "translation provider failed unexpectedly",
    }
    assert "en:" in (pipeline.status.error or "")


@pytest.mark.asyncio
async def test_target_timeout_is_bounded_and_keeps_other_translation() -> None:
    events: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    translator = ControllableTranslator(stall="en")
    pipeline = PipelineWithTranslator(
        publish, Settings(translation_target_timeout_seconds=0.05), translator,
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="timeout", version=1, stage=CaptionStage.STABLE,
            text="복음", source_language="ko", start_ms=0, end_ms=100,
        ))
        await asyncio.wait_for(committed.wait(), timeout=1)
    finally:
        await pipeline.stop()

    assert events[-1].translations == {"ja": "ja: 복음"}
    assert "timed out" in pipeline.status.errors_by_language["en"]
    assert translator.active == 0


@pytest.mark.asyncio
async def test_low_resource_serializes_model_inference_with_same_outputs() -> None:
    events: list[TranscriptEvent] = []
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    translator = ControllableTranslator()
    pipeline = PipelineWithTranslator(publish, Settings(), translator)
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo", resource_profile="low",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="low", version=1, stage=CaptionStage.STABLE,
            text="말씀", source_language="ko", start_ms=0, end_ms=100,
        ))
        await asyncio.wait_for(committed.wait(), timeout=1)
    finally:
        await pipeline.stop()

    assert translator.max_active == 1
    assert events[-1].translations == {"en": "en: 말씀", "ja": "ja: 말씀"}


class _BlockingFirstSegmentTranslator:
    provider = "fake"
    model = "blocking-first-segment"
    def __init__(self) -> None:
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def prepare(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def translate(
        self, text: str, *, source_language: str, target_language: str,
        context: SessionContext,
    ) -> str:
        del source_language, context
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            if text == "first":
                self.blocked.set()
                await self.release.wait()
            return f"{target_language}: {text}"
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_bounded_segment_workers_prevent_first_utterance_head_of_line_block() -> None:
    sent: list[TranscriptEvent] = []
    later_done = asyncio.Event()
    translator = _BlockingFirstSegmentTranslator()

    async def publish(event: TranscriptEvent) -> None:
        sent.append(event)
        if event.segment_id == "second" and event.stage is CaptionStage.COMMITTED:
            later_done.set()

    pipeline = PipelineWithTranslator(
        publish, Settings(postprocess_max_parallel_segments=2), translator
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="demo",
    ))
    try:
        for ident, word in [("first", "first"), ("second", "second")]:
            await pipeline.ingest(TranscriptEvent(
                segment_id=ident, version=1, stage=CaptionStage.STABLE,
                source_language="ko", text=word, start_ms=0, end_ms=100,
            ))
        await asyncio.wait_for(translator.blocked.wait(), timeout=1)
        await asyncio.wait_for(later_done.wait(), timeout=1)
        assert any(x.segment_id == "second" and x.committed for x in sent)
        assert not any(x.segment_id == "first" and x.committed for x in sent)
        assert translator.max_active <= 2
    finally:
        translator.release.set()
        await pipeline.stop()
    assert pipeline._worker_task is None
    assert pipeline._extra_workers == []


@pytest.mark.asyncio
async def test_low_resource_segment_budget_preserves_serial_inference() -> None:
    sent: list[TranscriptEvent] = []
    translator = _BlockingFirstSegmentTranslator()
    second_done = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        sent.append(event)
        if event.segment_id == "second" and event.committed:
            second_done.set()

    pipeline = PipelineWithTranslator(
        publish, Settings(postprocess_max_parallel_segments=4), translator
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="demo", resource_profile="low",
    ))
    try:
        for ident in ("first", "second"):
            await pipeline.ingest(TranscriptEvent(
                segment_id=ident, version=1, stage=CaptionStage.STABLE,
                source_language="ko", text=ident, start_ms=0, end_ms=100,
            ))
        await asyncio.wait_for(translator.blocked.wait(), timeout=1)
        assert not second_done.is_set()
        assert len(pipeline._extra_workers) == 0
        translator.release.set()
        await asyncio.wait_for(second_done.wait(), timeout=1)
        assert translator.max_active == 1
    finally:
        translator.release.set()
        await pipeline.stop()


class _FailOnceSegmentPipeline(CaptionPipeline):
    async def _process_stable(self, event: TranscriptEvent) -> None:
        if event.segment_id == "broken":
            raise RuntimeError("simulated optional postprocessor failure")
        await super()._process_stable(event)


@pytest.mark.asyncio
async def test_unexpected_segment_failure_does_not_kill_remaining_worker() -> None:
    events: list[TranscriptEvent] = []
    second_done = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)
        if event.segment_id == "good" and event.committed:
            second_done.set()

    pipeline = _FailOnceSegmentPipeline(
        publish, Settings(postprocess_max_parallel_segments=1)
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="demo",
    ))
    try:
        for ident in ("broken", "good"):
            await pipeline.ingest(TranscriptEvent(
                segment_id=ident, version=1, stage=CaptionStage.STABLE,
                source_language="ko", text=ident, start_ms=0, end_ms=100,
            ))
        await asyncio.wait_for(second_done.wait(), timeout=1)
        assert pipeline.failed_postprocess_segments == 1
        broken = [event for event in events if event.segment_id == "broken"]
        assert [event.stage for event in broken] == [
            CaptionStage.STABLE, CaptionStage.COMMITTED,
        ]
        assert broken[-1].translation_states == {"en": "failed"}
        assert broken[-1].text == "broken"
        assert broken[-1].committed is True
        assert any(event.segment_id == "good" and event.committed
                   for event in events)
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_first_target_publishes_before_second_target_completes() -> None:
    published: list[TranscriptEvent] = []
    early = asyncio.Event()
    finished = asyncio.Event()

    class _StalledTarget(ControllableTranslator):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()

        async def translate(
            self, text: str, *, source_language: str,
            target_language: str, context: SessionContext,
        ) -> str:
            if target_language == "ja":
                await self.release.wait()
            return await super().translate(
                text, source_language=source_language,
                target_language=target_language, context=context,
            )

    provider = _StalledTarget()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.TRANSLATED and event.translations.get("en"):
            early.set()
        if event.committed:
            finished.set()

    pipeline = PipelineWithTranslator(publish, Settings(), provider)
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="progressive", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="은혜", start_ms=0, end_ms=100,
        ))
        await asyncio.wait_for(early.wait(), timeout=1)
        assert not finished.is_set()
        interim = published[-1]
        assert interim.translation_states == {"en": "available", "ja": "pending"}
        assert interim.translations == {"en": "en: 은혜"}
        provider.release.set()
        await asyncio.wait_for(finished.wait(), timeout=1)
    finally:
        provider.release.set()
        await pipeline.stop()
    assert published[-1].translation_states == {
        "en": "available", "ja": "available"
    }
    assert published[-1].translations == {"en": "en: 은혜", "ja": "ja: 은혜"}
    assert [item.version for item in published] == sorted(
        item.version for item in published
    )


@pytest.mark.asyncio
async def test_target_failure_is_visible_before_later_language_succeeds() -> None:
    published: list[TranscriptEvent] = []
    failed_early = asyncio.Event()

    class _FailureThenStall(ControllableTranslator):
        def __init__(self) -> None:
            super().__init__(fail="en")
            self.release = asyncio.Event()

        async def translate(
            self, text: str, *, source_language: str,
            target_language: str, context: SessionContext,
        ) -> str:
            if target_language == "ja":
                await self.release.wait()
            return await super().translate(
                text, source_language=source_language,
                target_language=target_language, context=context,
            )

    provider = _FailureThenStall()

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.translation_states.get("en") == "failed":
            failed_early.set()

    pipeline = PipelineWithTranslator(publish, Settings(), provider)
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="partial-failure", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="말씀", start_ms=0, end_ms=100,
        ))
        await asyncio.wait_for(failed_early.wait(), timeout=1)
        assert published[-1].translation_states["ja"] == "pending"
        provider.release.set()
        for _ in range(500):
            if published[-1].committed:
                break
            await asyncio.sleep(0.001)
        assert published[-1].committed
    finally:
        provider.release.set()
        await pipeline.stop()
    assert published[-1].translations == {"ja": "ja: 말씀"}
    assert published[-1].translation_states == {
        "en": "failed", "ja": "available"
    }


@pytest.mark.asyncio
async def test_pipeline_stop_cancels_all_progressive_target_tasks() -> None:
    done = asyncio.Event()
    translator = ControllableTranslator(stall="en")
    pipeline = PipelineWithTranslator(
        lambda event: done.set() if False else asyncio.sleep(0),
        Settings(postprocess_drain_timeout_seconds=0.05),
        translator,
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    await pipeline.ingest(TranscriptEvent(
        segment_id="shutdown-targets", version=1, stage=CaptionStage.STABLE,
        source_language="ko", text="복음", start_ms=0, end_ms=100,
    ))
    for _ in range(500):
        if "en" in translator.started:
            break
        await asyncio.sleep(0.001)
    assert "en" in translator.started
    await asyncio.wait_for(pipeline.stop(), timeout=0.5)
    assert translator.active == 0


@pytest.mark.asyncio
async def test_replayed_stable_and_late_partial_cannot_regress_committed_source() -> None:
    from langtextflow.store import CaptionStore

    store = CaptionStore()
    done = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)
        if event.segment_id == "replay" and event.committed:
            done.set()

    pipeline = CaptionPipeline(publish, Settings())
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="none",
    ))
    try:
        partial = TranscriptEvent(
            segment_id="replay", version=1, stage=CaptionStage.PARTIAL,
            source_language="ko", text="은", start_ms=0,
        )
        stable = partial.model_copy(update={
            "version": 2, "stage": CaptionStage.STABLE, "text": "은혜",
            "end_ms": 500,
        })
        await pipeline.ingest(partial)
        await pipeline.ingest(stable)
        await asyncio.wait_for(done.wait(), timeout=1)
        # These would trigger CaptionStore's backwards-stage/immutable checks
        # without the source-admission fence.
        await pipeline.ingest(stable.model_copy(update={"version": 10}))
        await pipeline.ingest(partial.model_copy(update={"version": 11}))
        assert pipeline.duplicate_asr_events_dropped == 2
        assert pipeline.queue_depth == 0
        persisted = store.snapshot()
        assert len(persisted) == 1
        assert persisted[0].committed
        assert persisted[0].text == "은혜"
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_identical_repeated_speech_with_new_segment_id_is_not_deduped() -> None:
    from langtextflow.store import CaptionStore

    store = CaptionStore()
    committed: set[str] = set()
    both = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)
        if event.committed:
            committed.add(event.segment_id)
            if len(committed) == 2:
                both.set()

    pipeline = CaptionPipeline(publish, Settings())
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="none",
    ))
    try:
        for segment_id in ("first-amen", "second-amen"):
            await pipeline.ingest(TranscriptEvent(
                segment_id=segment_id, version=1, stage=CaptionStage.STABLE,
                source_language="ko", text="아멘", start_ms=0, end_ms=200,
            ))
        await asyncio.wait_for(both.wait(), timeout=1)
        assert committed == {"first-amen", "second-amen"}
        assert pipeline.duplicate_asr_events_dropped == 0
        assert len(store.snapshot()) == 2
    finally:
        await pipeline.stop()

@pytest.mark.asyncio
async def test_postprocessing_queue_overflow_emits_terminal_source_only_status() -> None:
    """An overloaded worker must not strand translated audience tracks pending."""
    events: list[TranscriptEvent] = []
    first_corrected = asyncio.Event()
    release = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)
        if event.segment_id == "busy" and event.stage is CaptionStage.CORRECTED:
            first_corrected.set()
            await release.wait()

    pipeline = CaptionPipeline(
        publish,
        Settings(postprocess_max_parallel_segments=1),
    )
    await pipeline.start(StartSessionRequest(
        source_language="en",
        target_languages=["ko", "ja"],
        translation_provider="demo",
    ))
    def stable(identity: str) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id=identity,
            stage=CaptionStage.STABLE,
            version=1,
            source_language="en",
            text=f"Original sermon {identity}",
            start_ms=0,
            end_ms=500,
        )

    try:
        await pipeline.ingest(stable("busy"))
        await asyncio.wait_for(first_corrected.wait(), 1)
        # Fill the production-size bounded queue while the sole worker is
        # deliberately blocked in publish, then force exactly one overflow.
        for index in range(pipeline.queue_capacity):
            await pipeline.ingest(stable(f"queued-{index}"))
        await pipeline.ingest(stable("overflow"))
        overflow_events = [item for item in events if item.segment_id == "overflow"]
        assert [event.stage for event in overflow_events] == [
            CaptionStage.STABLE, CaptionStage.COMMITTED,
        ]
        assert overflow_events[-1].committed is True
        assert overflow_events[-1].text == "Original sermon overflow"
        assert overflow_events[-1].translations == {}
        assert overflow_events[-1].translation_states == {
            "ko": "failed", "ja": "failed",
        }
        assert overflow_events[-1].version > overflow_events[0].version
        assert pipeline.dropped_postprocess_segments == 1
    finally:
        release.set()
        await asyncio.wait_for(pipeline.stop(), 2)

    assert any(event.segment_id == "queued-0" and event.committed for event in events)


@pytest.mark.asyncio
async def test_source_only_overflow_is_not_mislabeled_as_translation_failure() -> None:
    """If translation was disabled, no target should appear to have run."""
    events: list[TranscriptEvent] = []

    async def publish(event: TranscriptEvent) -> None:
        events.append(event)

    pipeline = CaptionPipeline(publish, Settings())
    pipeline.request = StartSessionRequest(
        source_language="ja", target_languages=["ko"],
        translation_provider="none",
    )
    pipeline._queue = asyncio.Queue(maxsize=1)
    pipeline._queue.put_nowait(None)
    await pipeline.ingest(TranscriptEvent(
        segment_id="overloaded-source-only",
        stage=CaptionStage.STABLE,
        version=1,
        source_language="ja",
        text="原文",
        start_ms=0,
    ))
    assert events[-1].stage is CaptionStage.COMMITTED
    assert events[-1].translation_states == {"ko": "unsupported"}
    assert events[-1].translations == {}


class _PartiallySuccessfulThenFailingPipeline(PipelineWithTranslator):
    async def _process_stable(self, event: TranscriptEvent) -> None:
        if event.segment_id != "partially-failed":
            await super()._process_stable(event)
            return
        partial_result = self._next_event(
            event,
            version=event.version + 1,
            stage=CaptionStage.TRANSLATED,
            text=event.text,
            translations={"en": "grace"},
            translation_states={"en": "available", "ja": "pending"},
        )
        await self._publish_postprocess(partial_result)
        raise RuntimeError("unexpected failure after successful target")


@pytest.mark.asyncio
async def test_unexpected_failure_preserves_successful_target_and_finalizes_pending() -> None:
    from langtextflow.store import CaptionStore

    events: list[TranscriptEvent] = []
    store = CaptionStore()
    terminal = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)
        events.append(event)
        if event.segment_id == "partially-failed" and event.committed:
            terminal.set()

    pipeline = _PartiallySuccessfulThenFailingPipeline(
        publish, Settings(), ControllableTranslator(),
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en", "ja"],
        translation_provider="demo",
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="partially-failed", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="은혜", start_ms=0, end_ms=200,
        ))
        await asyncio.wait_for(terminal.wait(), timeout=1)
        result = store.snapshot()[0]
        assert result.committed is True
        assert result.version == 3
        assert result.translations == {"en": "grace"}
        assert result.translation_states == {"en": "available", "ja": "failed"}
        assert result.text == "은혜"
        assert pipeline.failed_postprocess_segments == 1
        assert pipeline.queue_depth == 0
        assert pipeline._worker_progress == {}
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_stop_timeout_finalizes_active_and_queued_sources_with_no_pending_targets() -> None:
    from langtextflow.store import CaptionStore

    events: list[TranscriptEvent] = []
    store = CaptionStore()
    translator = ControllableTranslator(stall="en")

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)
        events.append(event)

    pipeline = PipelineWithTranslator(
        publish, Settings(
            postprocess_max_parallel_segments=1,
            postprocess_drain_timeout_seconds=0.05,
            translation_target_timeout_seconds=10.0,
        ), translator,
    )
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="demo", resource_profile="low",
    ))
    for identity in ("active", "queued"):
        await pipeline.ingest(TranscriptEvent(
            segment_id=identity, version=1, stage=CaptionStage.STABLE,
            text="말씀", source_language="ko", start_ms=0, end_ms=100,
        ))
    for _ in range(100):
        if translator.started:
            break
        await asyncio.sleep(0.001)
    assert "en" in translator.started
    await asyncio.wait_for(pipeline.stop(), timeout=1.0)

    assert translator.active == 0
    assert pipeline.expired_postprocess_segments == 2
    assert pipeline.queue_depth == 0
    assert len(store.snapshot()) == 2
    for record in store.snapshot():
        assert record.committed is True
        assert record.text == "말씀"
        assert record.translations == {}
        assert record.translation_states == {"en": "timed_out"}
        versions = [item.version for item in events if item.segment_id == record.segment_id]
        assert versions == sorted(versions)
    assert pipeline._worker_progress == {}


@pytest.mark.asyncio
async def test_failed_source_publish_does_not_poison_stable_dedupe() -> None:
    published: list[TranscriptEvent] = []
    attempts = 0
    committed = asyncio.Event()

    async def publish(event: TranscriptEvent) -> None:
        nonlocal attempts
        if event.stage is CaptionStage.STABLE:
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient publication failure")
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    pipeline = CaptionPipeline(publish, Settings())
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"],
        translation_provider="demo",
    ))
    event = TranscriptEvent(
        segment_id="retry-stable", version=1, stage=CaptionStage.STABLE,
        source_language="ko", text="복음", start_ms=0, end_ms=100,
    )
    try:
        with pytest.raises(RuntimeError, match="transient publication failure"):
            await pipeline.ingest(event)
        await pipeline.ingest(event)
        await asyncio.wait_for(committed.wait(), timeout=1.0)
    finally:
        await pipeline.stop()

    assert attempts == 2
    assert [item.stage for item in published].count(CaptionStage.STABLE) == 1
    assert published[-1].committed is True
    assert pipeline.duplicate_asr_events_dropped == 0
