from __future__ import annotations

import asyncio
import statistics
from collections import OrderedDict, deque
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic

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
        self.expired_postprocess_segments = 0
        # A final ASR segment may be replayed/duplicated by a provider. Keep a
        # bounded admission ledger so later PARTIAL/STABLE cannot regress its
        # already corrected/translated source. Distinct IDs are never deduped
        # by text, preserving legitimate repeated sentences.
        self._seen_stable_sources: OrderedDict[str, None] = OrderedDict()
        # The bounded replay ledger may evict old IDs; admitted work must
        # remain protected from duplicate publication until it is finalized.
        self._active_stable_sources: set[str] = set()
        self.duplicate_asr_events_dropped = 0
        self.revised_asr_events_rejected = 0
        self._stable_source_text: OrderedDict[str, str] = OrderedDict()
        # Latest successfully published event for each active worker.
        # A failed postprocessor must never regress an already translated
        # language, or leave an unfinished language pending forever.
        self._worker_progress: dict[str, TranscriptEvent] = {}
        self._source_revisions: dict[str, int] = {}
        self._queue_admitted_at: dict[str, float] = {}
        self._queue_lag_ms: deque[float] = deque(maxlen=512)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_lag_p95_ms(self) -> float:
        if not self._queue_lag_ms:
            return 0.0
        ordered = sorted(self._queue_lag_ms)
        return ordered[(95 * (len(ordered) - 1)) // 100]

    @property
    def queue_lag_p50_ms(self) -> float:
        return statistics.median(self._queue_lag_ms) if self._queue_lag_ms else 0.0

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
                target not in supported or target == request.source_language
                for target in request.target_languages
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
        self.expired_postprocess_segments = 0
        self._seen_stable_sources.clear()
        self._stable_source_text.clear()
        self._active_stable_sources.clear()
        self.duplicate_asr_events_dropped = 0
        self.revised_asr_events_rejected = 0
        self._worker_progress.clear()
        self._source_revisions.clear()
        self._queue_admitted_at.clear()
        self._queue_lag_ms.clear()
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

    async def ingest(
        self, event: TranscriptEvent, *, is_current=None,
    ) -> None:
        if (
            event.stage in (CaptionStage.PARTIAL, CaptionStage.STABLE)
            and (
                event.segment_id in self._seen_stable_sources
                or event.segment_id in self._active_stable_sources
            )
        ):
            if event.stage is CaptionStage.STABLE and (
                previous_text := self._stable_source_text.get(event.segment_id)
            ) is not None and previous_text != event.text:
                # A changed STABLE is not an exact replay. Until revision-aware
                # cancellation is implemented, reject it rather than publish a
                # new source over a potentially committed translation.
                self.revised_asr_events_rejected += 1
            else:
                self.duplicate_asr_events_dropped += 1
            return
        if event.stage is CaptionStage.STABLE:
            # Admit the first stable event by *segment identity* before
            # awaiting publish. A duplicate arriving during its await cannot
            # launch a second correction/MT worker for the same ID.
            self._seen_stable_sources[event.segment_id] = None
            self._source_revisions[event.segment_id] = event.source_revision
            self._stable_source_text[event.segment_id] = event.text
            self._active_stable_sources.add(event.segment_id)
            if len(self._seen_stable_sources) > 8192:
                old_id, _ = self._seen_stable_sources.popitem(last=False)
                if old_id not in self._active_stable_sources:
                    self._stable_source_text.pop(old_id, None)
                    self._source_revisions.pop(old_id, None)
        try:
            await self.publish(event)
        except BaseException:
            # Admission precedes the await to fence concurrent duplicate STABLE
            # events. If publication fails, release the reservation so the ASR
            # provider can retry the same segment instead of losing it forever.
            if event.stage is CaptionStage.STABLE:
                self._seen_stable_sources.pop(event.segment_id, None)
                self._stable_source_text.pop(event.segment_id, None)
                self._source_revisions.pop(event.segment_id, None)
                self._active_stable_sources.discard(event.segment_id)
            raise
        if event.stage is CaptionStage.STABLE:
            # Source publication may suspend across a session restart.
            # Do not admit optional work from a revoked ASR generation.
            if is_current is not None and not is_current():
                self._seen_stable_sources.pop(event.segment_id, None)
                self._active_stable_sources.discard(event.segment_id)
                return
            try:
                self._queue.put_nowait(event)
                self._queue_admitted_at[event.segment_id] = monotonic()
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
                try:
                    await self.publish(terminal)
                finally:
                    self._active_stable_sources.discard(event.segment_id)

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
        # No further workers can read this admission queue. Publish explicit
        # timeout outcomes for STABLE sources admitted but never processed.
        # Otherwise a stopped session can leave dozens of target tracks
        # permanently pending with no visible explanation. Bound the *whole*
        # remaining publication budget, not 200 ms per queued utterance.
        close_deadline = asyncio.get_running_loop().time() + 1.0
        while True:
            try:
                pending = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if pending is not None and pending.source_revision == self._source_revisions.get(
                    pending.segment_id, pending.source_revision
                ):
                    self.expired_postprocess_segments += 1
                    remaining = close_deadline - asyncio.get_running_loop().time()
                    if remaining > 0:
                        # Publishing failure is visible in the session's
                        # persistence/socket diagnostics; never block shutdown.
                        with suppress(Exception):
                            await asyncio.wait_for(
                                self._finalize_failed_segment(
                                    pending, pending, pending_state="timed_out",
                                ),
                                timeout=remaining,
                            )
            finally:
                if pending is not None:
                    self._queue_admitted_at.pop(pending.segment_id, None)
                    if pending.source_revision == self._source_revisions.get(
                        pending.segment_id, pending.source_revision
                    ):
                        self._active_stable_sources.discard(pending.segment_id)
                self._queue.task_done()
        self._worker_progress.clear()
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
            admitted_at = self._queue_admitted_at.pop(event.segment_id, None)
            if admitted_at is not None:
                self._queue_lag_ms.append((monotonic() - admitted_at) * 1000)
            try:
                # A newer source epoch may supersede an event while it waits
                # behind other segments in the bounded postprocess queue.
                # Never spend scarce CPU inference capacity on that stale work.
                if event.source_revision != self._source_revisions.get(
                    event.segment_id, event.source_revision
                ):
                    continue
                await self._process_stable(event)
            except asyncio.CancelledError:
                # Stop-drain expired while optional inference was running.
                # A source caption is already visible; explicitly finalize
                # pending targets, retaining any successfully emitted ones.
                if event.source_revision != self._source_revisions.get(
                    event.segment_id, event.source_revision
                ):
                    raise
                self.expired_postprocess_segments += 1
                latest = self._worker_progress.get(event.segment_id, event)
                with suppress(Exception):
                    await asyncio.wait_for(
                        self._finalize_failed_segment(
                            event, latest, pending_state="timed_out",
                        ),
                        timeout=0.2,
                    )
                raise
            except Exception:
                # Optional providers and progress publication must not kill the
                # whole realtime worker. Preserve already published language
                # results, terminate remaining pending tracks and move on.
                if event.source_revision != self._source_revisions.get(
                    event.segment_id, event.source_revision
                ):
                    continue
                self.failed_postprocess_segments += 1
                self.status.error = (
                    "postprocessing failed unexpectedly; source caption preserved"
                )
                latest = self._worker_progress.get(event.segment_id, event)
                # The publication sink itself may be unavailable. Count
                # the segment failure but never kill the worker or loop.
                with suppress(Exception):
                    await self._finalize_failed_segment(event, latest)
            finally:
                if event.source_revision == self._source_revisions.get(
                    event.segment_id, event.source_revision
                ):
                    self._worker_progress.pop(event.segment_id, None)
                if event.source_revision == self._source_revisions.get(
                    event.segment_id, event.source_revision
                ):
                    self._active_stable_sources.discard(event.segment_id)
                self._queue.task_done()

    async def _publish_postprocess(self, event: TranscriptEvent) -> None:
        # A superseded source epoch must never publish an obsolete correction,
        # translation or commit. This is a second fence beyond store validation:
        # the async publication sink may otherwise fail after observing a
        # stale event, taking down a healthy postprocess worker.
        latest_revision = self._source_revisions.get(event.segment_id, 1)
        if event.source_revision != latest_revision:
            return
        await self.publish(event)
        if event.source_revision == self._source_revisions.get(event.segment_id, 1):
            self._worker_progress[event.segment_id] = event

    async def _finalize_failed_segment(
        self, source: TranscriptEvent, latest: TranscriptEvent, *,
        pending_state: str = "failed",
    ) -> None:
        if latest.committed:
            return
        targets = self.request.target_languages if self.request else []
        states = dict(latest.translation_states)
        translations = dict(latest.translations)
        for language in targets:
            if language in translations:
                states[language] = "available"
            elif states.get(language) not in {"failed", "timed_out", "unsupported"}:
                states[language] = (
                    pending_state if self.translator is not None else "unsupported"
                )
        terminal = self._next_event(
            latest, version=latest.version + 1, stage=CaptionStage.COMMITTED,
            text=latest.text, translations=translations,
            translation_states=states, committed=True,
        )
        # The source STABLE caption has already been sent by ingest().
        # The terminal is always monotonically later than last published.
        await self._publish_postprocess(terminal)

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
        except Exception:
            # An unexpected optional LLM/provider failure must not discard
            # the deterministic correction or skip downstream translation.
            reason = "correction provider failed unexpectedly; using deterministic result"
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
        await self._publish_postprocess(corrected)

        translations: dict[str, str] = {}
        base = corrected
        if enabled:
            assert self.translator is not None
            limiter = self._translation_limiter

            async def translate_target(target: str) -> tuple[str, str | None, str | None]:
                try:
                    # The deadline includes time waiting for a CPU inference
                    # slot, not just the provider call. Otherwise queued
                    # targets can remain pending indefinitely under overload.
                    async with asyncio.timeout(
                        self.settings.translation_target_timeout_seconds
                    ):
                        async with limiter:
                            translated = await self.translator.translate(
                                corrected_text,
                                source_language=self.request.source_language,
                                target_language=target,
                                context=self.request.context,
                            )
                            if not translated.strip():
                                raise TranslationError(
                                    "translation provider returned empty text"
                                )
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
                    await self._publish_postprocess(base)
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
        await self._publish_postprocess(committed)

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
            source_revision=previous.source_revision,
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
