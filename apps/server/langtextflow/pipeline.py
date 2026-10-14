from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from datetime import UTC, datetime

from .config import Settings
from .correction import DeterministicCorrector
from .llm_correction import (
    ConstrainedCorrector,
    CorrectionError,
    OllamaConstrainedCorrector,
)
from .models import (
    CaptionStage,
    CorrectionProvenance,
    CorrectionStatus,
    StartSessionRequest,
    TranscriptEvent,
    TranslationStatus,
)
from .translation import (
    DemoTranslator,
    M2M100CT2Translator,
    OllamaTranslator,
    OpenAICompatibleTranslator,
    TranslationError,
    Translator,
)


class CaptionPipeline:
    """Bounded post-ASR worker with source-first publication and target isolation.

    Independent speech segments use a bounded number of workers. The target
    inference semaphore is global for the entire session (not per segment).
    """

    def __init__(self, publish, settings: Settings) -> None:
        self.publish = publish
        self.settings = settings
        self.deterministic_corrector = DeterministicCorrector()
        self.request: StartSessionRequest | None = None
        self.llm_corrector: ConstrainedCorrector | None = None
        self.translator: Translator | None = None
        self.correction_status = CorrectionStatus()
        self.status = TranslationStatus()
        self._queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(maxsize=128)
        self._worker_task: asyncio.Task[None] | None = None
        self._extra_workers: list[asyncio.Task[None]] = []
        self._translation_limiter = asyncio.Semaphore(1)
        self.dropped_postprocess_segments = 0
        self.failed_postprocess_segments = 0
        # A final ASR segment may be replayed/duplicated by a provider. Keep a
        # bounded admission ledger so later PARTIAL/STABLE cannot regress its
        # already corrected/translated source. Distinct IDs are never deduped
        # by text, preserving legitimate repeated sentences.
        self._seen_stable_sources: OrderedDict[str, None] = OrderedDict()
        self.duplicate_asr_events_dropped = 0

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    @staticmethod
    def validate_provider_configuration(request: StartSessionRequest) -> None:
        """Reject invalid provider IDs/pairs before interrupting a live session.

        Availability/quality is handled separately by preflight and prepare();
        this check never instantiates or downloads a model.
        """
        if request.correction_provider not in {"none", "ollama"}:
            raise ValueError(
                f"unsupported correction provider: {request.correction_provider}"
            )
        if request.translation_provider not in {
            "none", "demo", "ollama", "openai-compatible", "m2m100-ct2",
        }:
            raise ValueError(
                f"unsupported translation provider: {request.translation_provider}"
            )
        CaptionPipeline._validate_local_translation_pairs(request)

    @staticmethod
    def _validate_local_translation_pairs(request: StartSessionRequest) -> None:
        if request.translation_provider == "m2m100-ct2":
            supported = {"ko", "en", "ja"}
            if request.source_language not in supported or any(
                target not in supported for target in request.target_languages
            ):
                raise ValueError(
                    "Local M2M100 supports only the six directed ko/en/ja pairs"
                )

    async def start(self, request: StartSessionRequest) -> None:
        # Direct pipeline test doubles are allowed to supply a fake provider;
        # runtime validates the real advertised provider registry *before*
        # ending another session. Real builders remain strict.
        await self.stop()
        self.request = request
        self._queue = asyncio.Queue(maxsize=128)
        self.dropped_postprocess_segments = 0
        self.failed_postprocess_segments = 0
        self._seen_stable_sources.clear()
        self.duplicate_asr_events_dropped = 0
        low_resource = (
            self.settings.resource_profile == "low" or request.resource_profile == "low"
        )
        self._translation_limiter = asyncio.Semaphore(
            1 if low_resource else self.settings.translation_max_parallel_targets
        )
        worker_count = (
            1 if low_resource else self.settings.postprocess_max_parallel_segments
        )
        self.llm_corrector = self._build_corrector(request)
        self.translator = self._build_translator(request)
        self.correction_status = CorrectionStatus(
            enabled=self.llm_corrector is not None,
            provider=request.correction_provider,
            model=getattr(self.llm_corrector, "model", None),
            available=self.llm_corrector is not None,
        )
        self.status = TranslationStatus(
            enabled=self.translator is not None,
            provider=request.translation_provider,
            model=getattr(self.translator, "model", None),
            available=self.translator is not None,
        )
        if self.llm_corrector is not None:
            try:
                await self.llm_corrector.prepare()
            except CorrectionError as exc:
                self.correction_status.available = False
                self.correction_status.error = str(exc)
        if self.translator is not None:
            try:
                await self.translator.prepare()
            except TranslationError as exc:
                self.status.available = False
                self.status.error = str(exc)
        self._worker_task = asyncio.create_task(
            self._worker(), name="caption-postprocessing"
        )
        self._extra_workers = [
            asyncio.create_task(self._worker(), name=f"caption-postprocessing-{index}")
            for index in range(1, worker_count)
        ]

    async def ingest(self, event: TranscriptEvent) -> None:
        if (
            event.stage in (CaptionStage.PARTIAL, CaptionStage.STABLE)
            and event.segment_id in self._seen_stable_sources
        ):
            self.duplicate_asr_events_dropped += 1
            return
        if event.stage is CaptionStage.STABLE:
            # Admit the first stable event by *segment identity* before
            # awaiting publish. A duplicate arriving during its await cannot
            # launch a second correction/MT worker for the same ID.
            self._seen_stable_sources[event.segment_id] = None
            if len(self._seen_stable_sources) > 8192:
                self._seen_stable_sources.popitem(last=False)
        await self.publish(event)
        if event.stage is CaptionStage.STABLE:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                # Publish a terminal source-only outcome instead of leaving
                # this segment's translation tracks permanently "pending".
                # No correction/MT is attempted, and source text is immutable.
                self.dropped_postprocess_segments += 1
                self.status.error = "postprocessing queue full; source caption preserved"
                targets = self.request.target_languages if self.request else []
                terminal = self._next_event(
                    event,
                    version=event.version + 1,
                    stage=CaptionStage.COMMITTED,
                    text=event.text,
                    translation_states={
                        target: ("failed" if self.translator is not None else "unsupported")
                        for target in targets
                    },
                    committed=True,
                )
                await self.publish(terminal)

    async def stop(self) -> None:
        first_error: Exception | None = None
        tasks = [
            task for task in (self._worker_task, *self._extra_workers)
            if task is not None
        ]
        self._worker_task = None
        self._extra_workers = []
        if tasks:
            if any(not task.done() for task in tasks):
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._queue.join(),
                        timeout=self.settings.postprocess_drain_timeout_seconds,
                    )
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        self._queue = asyncio.Queue(maxsize=128)

        corrector = self.llm_corrector
        translator = self.translator
        self.llm_corrector = None
        self.translator = None
        self.request = None
        for provider in (corrector, translator):
            if provider is None:
                continue
            try:
                await provider.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _build_corrector(self, request: StartSessionRequest) -> ConstrainedCorrector | None:
        provider = request.correction_provider
        if provider == "none":
            return None
        if provider == "ollama":
            return OllamaConstrainedCorrector(
                base_url=self.settings.ollama_url,
                model=request.correction_model or self.settings.ollama_correction_model,
                request_timeout_seconds=max(self.settings.correction_timeout_seconds, 0.1),
            )
        raise ValueError(f"unsupported correction provider: {provider}")

    def _build_translator(self, request: StartSessionRequest) -> Translator | None:
        provider = request.translation_provider
        if provider == "none":
            return None
        if provider == "m2m100-ct2":
            # Preserve standalone builder validation used by the offline
            # adapter and its tests, independent from runtime lifecycle.
            self._validate_local_translation_pairs(request)
            return M2M100CT2Translator(
                model_dir=self.settings.local_m2m100_ct2_dir,
                tokenizer_dir=self.settings.local_m2m100_tokenizer_dir,
                cpu_threads=(
                    self.settings.low_resource_cpu_threads
                    if (request.resource_profile == "low"
                        or self.settings.resource_profile == "low")
                    else self.settings.local_m2m100_cpu_threads
                ),
            )
        if provider == "demo":
            return DemoTranslator()
        if provider == "ollama":
            return OllamaTranslator(
                base_url=self.settings.ollama_url,
                model=request.translation_model or self.settings.ollama_translation_model,
                request_timeout_seconds=self.settings.ollama_translation_timeout_seconds,
            )
        if provider == "openai-compatible":
            return OpenAICompatibleTranslator(
                base_url=self.settings.openai_compatible_url,
                model=(
                    request.translation_model
                    or self.settings.openai_compatible_translation_model
                ),
                api_key=self.settings.openai_compatible_api_key,
                request_timeout_seconds=max(
                    self.settings.openai_compatible_timeout_seconds,
                    0.1,
                ),
            )
        raise ValueError(f"unsupported translation provider: {provider}")

    async def _worker(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                self._queue.task_done()
                return
            try:
                await self._process_stable(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A broken optional provider must not permanently kill the
                # segment worker and therefore the entire live ASR session.
                self.failed_postprocess_segments += 1
                self.status.error = (
                    "postprocessing failed unexpectedly; source caption preserved"
                )
            finally:
                self._queue.task_done()

    async def _correct(self, text: str) -> tuple[str, CorrectionProvenance]:
        assert self.request is not None
        deterministic = self.deterministic_corrector.correct(
            text, self.request.context, source_language=self.request.source_language
        )
        deterministic_changed = deterministic != text
        provider = self.request.correction_provider
        model = getattr(self.llm_corrector, "model", None)

        if self.llm_corrector is None:
            return deterministic, CorrectionProvenance(
                method="deterministic",
                deterministic_changed=deterministic_changed,
                changed=deterministic_changed,
            )

        if not self.correction_status.available:
            return deterministic, CorrectionProvenance(
                method="fallback",
                provider=provider,
                model=model,
                deterministic_changed=deterministic_changed,
                changed=deterministic_changed,
                fallback_reason=self.correction_status.error or "correction provider unavailable",
            )

        try:
            corrected = await asyncio.wait_for(
                self.llm_corrector.correct(
                    deterministic,
                    source_language=self.request.source_language,
                    context=self.request.context,
                ),
                timeout=max(self.settings.correction_timeout_seconds, 0.1),
            )
            self.correction_status.error = None
            return corrected, CorrectionProvenance(
                method="llm",
                provider=provider,
                model=model,
                deterministic_changed=deterministic_changed,
                llm_attempted=True,
                llm_applied=True,
                changed=corrected != text,
            )
        except TimeoutError:
            reason = (
                f"correction timed out after {self.settings.correction_timeout_seconds:.1f}s; "
                "using deterministic result"
            )
            self.correction_status.error = reason
        except CorrectionError as exc:
            reason = f"{exc}; using deterministic result"
            self.correction_status.error = reason

        return deterministic, CorrectionProvenance(
            method="fallback",
            provider=provider,
            model=model,
            deterministic_changed=deterministic_changed,
            llm_attempted=True,
            llm_applied=False,
            changed=deterministic_changed,
            fallback_reason=reason,
        )

    async def _process_stable(self, event: TranscriptEvent) -> None:
        assert self.request is not None
        version = event.version + 1
        corrected_text, provenance = await self._correct(event.text)
        targets = self.request.target_languages
        enabled = self.translator is not None and self.status.available
        states: dict[str, str] = {
            language: (
                "pending" if enabled else
                "unsupported" if self.translator is None else "failed"
            )
            for language in targets
        }
        corrected = self._next_event(
            event,
            version=version,
            stage=CaptionStage.CORRECTED,
            text=corrected_text,
            correction=provenance,
            translation_states=states,
        )
        await self.publish(corrected)

        translations: dict[str, str] = {}
        base = corrected
        if enabled:
            assert self.translator is not None
            limiter = self._translation_limiter

            async def translate_target(target: str) -> tuple[str, str | None, str | None]:
                async with limiter:
                    try:
                        translated = await asyncio.wait_for(
                            self.translator.translate(
                                corrected_text,
                                source_language=self.request.source_language,
                                target_language=target,
                                context=self.request.context,
                            ),
                            timeout=self.settings.translation_target_timeout_seconds,
                        )
                        if not translated.strip():
                            raise TranslationError("translation provider returned empty text")
                        return target, translated.strip(), None
                    except TimeoutError:
                        return target, None, (
                            f"translation timed out after "
                            f"{self.settings.translation_target_timeout_seconds:.1f}s"
                        )
                    except TranslationError as exc:
                        return target, None, str(exc)
                    except Exception:
                        return target, None, "translation provider failed unexpectedly"

            tasks = [
                asyncio.create_task(
                    translate_target(target), name=f"caption-mt-{target}"
                ) for target in targets
            ]
            errors: dict[str, str] = {}
            try:
                # Publish each target's completion without awaiting unrelated
                # languages; send cumulative translation snapshots, not deltas.
                for result in asyncio.as_completed(tasks):
                    target, translated, error = await result
                    if error is not None:
                        states[target] = (
                            "timed_out" if error.startswith("translation timed out")
                            else "failed"
                        )
                        errors[target] = error
                    else:
                        assert translated is not None
                        states[target] = "available"
                        translations[target] = translated
                    self.status.errors_by_language = dict(errors)
                    self.status.error = "; ".join(
                        f"{language}: {reason}" for language, reason in errors.items()
                    ) or None
                    version += 1
                    base = self._next_event(
                        base,
                        version=version,
                        stage=CaptionStage.TRANSLATED,
                        text=corrected_text,
                        translations=translations,
                        translation_states=states,
                    )
                    await self.publish(base)
            finally:
                # as_completed() launches target tasks eagerly. On session
                # stop/restart, do not leave orphaned provider inference behind.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        version += 1
        committed = self._next_event(
            base,
            version=version,
            stage=CaptionStage.COMMITTED,
            text=corrected_text,
            translations=translations,
            translation_states=states,
            committed=True,
        )
        await self.publish(committed)

    @staticmethod
    def _next_event(
        previous: TranscriptEvent,
        *,
        version: int,
        stage: CaptionStage,
        text: str,
        translations: dict[str, str] | None = None,
        translation_states: dict[str, str] | None = None,
        correction: CorrectionProvenance | None = None,
        committed: bool = False,
    ) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id=previous.segment_id,
            version=version,
            stage=stage,
            source_language=previous.source_language,
            text=text,
            translations=dict(translations or {}),
            translation_states=dict(translation_states or {}),
            start_ms=previous.start_ms,
            end_ms=previous.end_ms,
            speaker=previous.speaker,
            confidence=previous.confidence,
            correction=correction if correction is not None else previous.correction,
            committed=committed,
            emitted_at=datetime.now(UTC),
        )
