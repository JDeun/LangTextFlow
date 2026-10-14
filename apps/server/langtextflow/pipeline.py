from __future__ import annotations

import asyncio
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

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    async def start(self, request: StartSessionRequest) -> None:
        await self.stop()
        self.request = request
        self._queue = asyncio.Queue(maxsize=128)
        self.dropped_postprocess_segments = 0
        self.failed_postprocess_segments = 0
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
        await self.publish(event)
        if event.stage is CaptionStage.STABLE:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                # Source has already been published. Preserve live ASR transport;
                # flag the absence of postprocessing rather than stalling audio.
                self.dropped_postprocess_segments += 1
                self.status.error = "postprocessing queue full; source caption preserved"

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
        corrected = self._next_event(
            event,
            version=version,
            stage=CaptionStage.CORRECTED,
            text=corrected_text,
            correction=provenance,
        )
        await self.publish(corrected)

        translations: dict[str, str] = {}
        if self.translator is not None and self.status.available:
            targets = self.request.target_languages
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
                        # Bad adapters must not tear down the caption worker.
                        # Do not expose raw provider exceptions to audience or logs.
                        return target, None, "translation provider failed unexpectedly"

            # A single slow target must not prevent another target starting.
            # The bounded semaphore protects low-spec CPUs from model overload.
            results = await asyncio.gather(*(translate_target(t) for t in targets))
            errors = {target: error for target, _, error in results if error is not None}
            self.status.errors_by_language = errors
            self.status.error = "; ".join(
                f"{target}: {error}" for target, error in errors.items()
            ) or None
            translations = {
                target: result for target, result, error in results
                if result is not None and error is None
            }

        base = corrected
        if translations:
            version += 1
            base = self._next_event(
                corrected,
                version=version,
                stage=CaptionStage.TRANSLATED,
                text=corrected_text,
                translations=translations,
            )
            await self.publish(base)

        version += 1
        committed = self._next_event(
            base,
            version=version,
            stage=CaptionStage.COMMITTED,
            text=corrected_text,
            translations=translations,
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
        correction: CorrectionProvenance | None = None,
        committed: bool = False,
    ) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id=previous.segment_id,
            version=version,
            stage=stage,
            source_language=previous.source_language,
            text=text,
            translations=translations or {},
            start_ms=previous.start_ms,
            end_ms=previous.end_ms,
            speaker=previous.speaker,
            confidence=previous.confidence,
            correction=correction if correction is not None else previous.correction,
            committed=committed,
            emitted_at=datetime.now(UTC),
        )
