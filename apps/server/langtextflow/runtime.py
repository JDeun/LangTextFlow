import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Event as ThreadEvent
from uuid import uuid4

from .asr import (
    AsrEngine,
    FasterWhisperStreamingAsrEngine,
    MockStreamingAsrEngine,
    ReplayFallbackAsrEngine,
    VibeVoiceStreamingAsrEngine,
)
from .asr.base import AsrEngineError
from .config import Settings, get_settings
from .hub import WebSocketHub
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    CaptionStage,
    CorrectionStatus,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
    TranslationStatus,
)
from .pipeline import CaptionPipeline
from .post_event_archive import PostEventArchive
from .recording_writer import SessionRecordingWriter
from .session_repository import SessionRepository
from .store import CaptionStore
from .telemetry import EnergyVad, RealtimeMetrics, RustAuthorityMetrics, latency_ms
from .whisper_cache import managed_whisper_cache

_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PERSISTENCE_FAILURE = "Local session storage unavailable; check disk and database health."


class SessionChangedError(RuntimeError):
    """A stop request belongs to a session that is no longer current."""


class CaptionRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = CaptionStore(max_segments=self.settings.max_segments)
        self.hub = WebSocketHub()
        self.state = SessionState()
        self.pipeline = CaptionPipeline(self._publish, self.settings)
        self.engine: AsrEngine | None = None
        # An epoch object is captured by each ASR source callback. Delayed
        # events from an older ASR provider can never enter a new session.
        self._active_generation: object | None = None
        self.stale_asr_events_dropped = 0
        self.history = SessionRepository(self.settings.database_path)
        self.archive = PostEventArchive(self.settings.database_path)
        self._recorder: SessionRecordingWriter | None = None
        self._recording_control: asyncio.Task | None = None
        self._recording_finalizer: asyncio.Task | None = None
        self._source_stop: asyncio.Task[SessionState] | None = None
        self._recording_phase = "idle"
        self._recording_result: dict | None = None
        self._audio_samples_received = 0
        self.metrics = RealtimeMetrics()
        self._vad = EnergyVad(
            threshold_dbfs=self.settings.vad_threshold_dbfs,
            hangover_frames=self.settings.vad_hangover_frames,
            max_frame_bytes=self.settings.max_audio_frame_bytes,
        )
        self._stage_times: dict[str, dict[CaptionStage, datetime]] = {}
        self._persistence_queue: asyncio.Queue[
            tuple[str, TranscriptEvent] | None
        ] = asyncio.Queue(maxsize=1024)
        self._persistence_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._rust_slot = asyncio.Semaphore(1)
        self._rust_session = False
        self._rust_relay_generation: object | None = None
        self._initialized = False
        self._recording_recovery_stop = ThreadEvent()
        self._recording_recovery_task: asyncio.Task[None] | None = None
        self._recording_recovery_batch: asyncio.Task[int] | None = None
        self._recording_recovery_closed = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await asyncio.to_thread(self.history.initialize)
        await asyncio.to_thread(self.archive.initialize)
        await asyncio.to_thread(self.history.recover_interrupted_sessions)
        self._initialized = True
        self.request_recording_recovery()

    def request_recording_recovery(self) -> None:
        if not self._initialized or self._recording_recovery_closed:
            return
        if self._recording_recovery_task is not None and not self._recording_recovery_task.done():
            return
        if self._recording_recovery_batch is not None and not self._recording_recovery_batch.done():
            return
        self._recording_recovery_stop = ThreadEvent()
        self._recording_recovery_task = asyncio.create_task(
            self._recover_recordings(self._recording_recovery_stop), name="recording-recovery",
        )

    async def _recover_recordings(self, stop: ThreadEvent) -> None:
        try:
            cutoff = datetime.now(UTC).isoformat()
            # At most one worker and 32 journal candidates per request/startup.
            # Remaining candidates can advance on the next archive refresh.
            for _ in range(4):
                self._recording_recovery_batch = asyncio.create_task(asyncio.to_thread(
                    self._recover_archive_batch, stop,
                    attempted_before=cutoff,
                ))
                self._recording_recovery_batch.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception(),
                )
                processed = await asyncio.shield(self._recording_recovery_batch)
                if stop.is_set() or processed < 8:
                    return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            stop.set()
            raise
        except Exception:
            logging.getLogger(__name__).warning("Recording recovery deferred: storage unavailable")

    def _recover_archive_batch(self, stop, *, attempted_before):
        # Private package ownership uses the same single bounded native worker.
        self.archive.packages.recover_one(stop)
        return self.archive.recording_journal.recover_batch(
            stop, attempted_before=attempted_before,
        )

    def _start_persistence_worker(self) -> None:
        if self._persistence_task is not None:
            return
        self._persistence_queue = asyncio.Queue(maxsize=1024)
        self._persistence_task = asyncio.create_task(
            self._persistence_worker(),
            name="session-persistence",
        )

    async def _stop_persistence_worker(self) -> None:
        task = self._persistence_task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                self._persistence_queue.join(),
                timeout=self.settings.persistence_drain_timeout_seconds,
            )
        except TimeoutError:
            # Preserve live captions; the archive can be incomplete. An
            # in-flight asyncio.to_thread SQLite write cannot be force-killed
            # and may still finish after its asyncio waiter is cancelled.
            # Therefore do not incorrectly count it as a confirmed drop.
            self.state.persistence_error = (
                "transcript persistence shutdown deadline exceeded; "
                "archive may be incomplete"
            )
            task.cancel()
            # Coroutine cancellation releases queue.task_done() in worker
            # finally; queued, *unstarted* writes cannot execute afterward.
            with suppress(asyncio.CancelledError, Exception):
                await task
            while True:
                try:
                    item = self._persistence_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    if item is not None:
                        self.metrics.persistence_events_dropped += 1
                finally:
                    self._persistence_queue.task_done()
        else:
            await self._persistence_queue.put(None)
            await task
        finally:
            self._persistence_task = None
            self._refresh_queue_metrics()

    async def shutdown(self) -> None:
        self._recording_recovery_closed = True
        self._recording_recovery_stop.set()
        try:
            await self.stop()
        finally:
            if self._recording_recovery_task is not None:
                with suppress(asyncio.CancelledError):
                    await self._recording_recovery_task
            if self._recording_recovery_batch is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(self._recording_recovery_batch)

    async def _publish(self, event: TranscriptEvent) -> None:
        self.store.apply(event)
        self._observe_event(event)
        # Each client owns a bounded FIFO sender. Never await a network write
        # while holding the ASR->source-caption processing path.
        self.hub.enqueue(event)
        if self._rust_session:
            # This is only a UI mirror. Rust has already arbitrated/persisted the event.
            return
        session_id = self.state.session_id
        if not session_id or self._persistence_task is None:
            self._refresh_queue_metrics()
            return
        try:
            self._persistence_queue.put_nowait((session_id, event))
        except asyncio.QueueFull:
            # The published caption remains visible; the archive is now
            # incomplete. Count the event loss rather than silently hiding it.
            self.metrics.persistence_events_dropped += 1
            self.state.persistence_error = "transcript persistence queue is full"
        self._refresh_queue_metrics()

    def _observe_event(self, event: TranscriptEvent) -> None:
        self.metrics.last_event_at = event.emitted_at
        # Partial captions never contribute to stable->correction/translation
        # latency. Keeping every partial-id forever would leak memory in long
        # events with interrupted or aborted ASR utterances.
        if event.stage is CaptionStage.PARTIAL:
            return
        timings = self._stage_times.setdefault(event.segment_id, {})
        timings[event.stage] = event.emitted_at
        # Retain recent timing information only: the durable caption store and
        # persisted transcript are independent from these optional telemetry
        # timestamps. dict preserves arrival insertion order in Python 3.11+.
        max_tracked = max(8, self.settings.max_segments * 2)
        if len(self._stage_times) > max_tracked:
            oldest_segment = next(iter(self._stage_times))
            self._stage_times.pop(oldest_segment, None)

        if event.stage is CaptionStage.STABLE:
            self.metrics.last_correction_latency_ms = None
            self.metrics.last_translation_latency_ms = None
            self.metrics.last_commit_latency_ms = None
            if self.state.started_at is not None and event.end_ms is not None:
                expected_end = self.state.started_at + timedelta(milliseconds=event.end_ms)
                self.metrics.last_asr_lag_ms = latency_ms(expected_end, event.emitted_at)
            return

        if event.stage is CaptionStage.CORRECTED:
            stable_at = timings.get(CaptionStage.STABLE)
            if stable_at is not None:
                self.metrics.last_correction_latency_ms = latency_ms(stable_at, event.emitted_at)
            return

        if event.stage is CaptionStage.TRANSLATED:
            corrected_at = timings.get(CaptionStage.CORRECTED)
            if corrected_at is not None:
                self.metrics.last_translation_latency_ms = latency_ms(
                    corrected_at,
                    event.emitted_at,
                )
            return

        if event.stage is CaptionStage.COMMITTED:
            stable_at = timings.get(CaptionStage.STABLE)
            if stable_at is not None:
                self.metrics.last_commit_latency_ms = latency_ms(stable_at, event.emitted_at)
            self._stage_times.pop(event.segment_id, None)

    async def _persistence_worker(self) -> None:
        while True:
            item = await self._persistence_queue.get()
            try:
                if item is None:
                    return
                session_id, event = item
                try:
                    await asyncio.to_thread(
                        self.history.upsert_segment,
                        session_id,
                        event,
                    )
                except Exception:
                    self.metrics.persistence_events_failed += 1
                    if self.state.session_id == session_id:
                        self.state.persistence_error = _PERSISTENCE_FAILURE
            finally:
                self._persistence_queue.task_done()
                self._refresh_queue_metrics()

    def _vibevoice_engine(self, publish=None) -> VibeVoiceStreamingAsrEngine:
        return VibeVoiceStreamingAsrEngine(
            publish or self.pipeline.ingest,
            base_url=self.settings.vibevoice_url,
            queue_chunks=self.settings.audio_queue_chunks,
            max_frame_bytes=self.settings.max_audio_frame_bytes,
        )

    def _faster_whisper_engine(
        self, publish=None, *, low_resource: bool | None = None,
    ) -> FasterWhisperStreamingAsrEngine:
        low = self.settings.resource_profile == "low" if low_resource is None else low_resource
        return FasterWhisperStreamingAsrEngine(
            publish or self.pipeline.ingest,
            model=(
                self.settings.low_resource_whisper_model
                if low else self.settings.faster_whisper_model
            ),
            device="cpu" if low else self.settings.faster_whisper_device,
            compute_type="int8" if low else self.settings.faster_whisper_compute_type,
            cpu_threads=self.settings.low_resource_cpu_threads if low else None,
            chunk_seconds=self.settings.faster_whisper_chunk_seconds,
            queue_chunks=min(8, self.settings.audio_queue_chunks)
            if low else self.settings.audio_queue_chunks,
            max_frame_bytes=self.settings.max_audio_frame_bytes,
            cache_dir=str(managed_whisper_cache(self.settings.model_cache_dir)),
        )

    async def _provider_changed(self, provider: str) -> None:
        self.state.engine = provider
        if self.state.session_id:
            try:
                await asyncio.to_thread(
                    self.history.update_engine,
                    self.state.session_id,
                    provider,
                )
            except Exception:
                self.state.persistence_error = _PERSISTENCE_FAILURE
        self._refresh_queue_metrics()

    def _build_engine(self, request: StartSessionRequest) -> AsrEngine:
        if self.settings.runtime_core == "rust":
            from .rust_runtime import RustRuntimeBridge, validate_rust_request

            validate_rust_request(self.settings, request)
            return RustRuntimeBridge(self._publish, self.settings, self._rust_slot)
        low = self.settings.resource_profile == "low" or request.resource_profile == "low"
        if request.engine == "mock":
            return MockStreamingAsrEngine(self.pipeline.ingest)
        if request.engine == "vibevoice":
            return self._vibevoice_engine()
        if request.engine == "faster-whisper":
            return self._faster_whisper_engine(low_resource=low)
        if request.engine == "auto":
            # Low-resource installations never probe/load a 7B sidecar in Auto.
            # Explicit "vibevoice" remains an intentional operator override.
            if low:
                return self._faster_whisper_engine(low_resource=True)
            return ReplayFallbackAsrEngine(
                self.pipeline.ingest,
                [
                    ("vibevoice", lambda publish: self._vibevoice_engine(publish)),
                    ("faster-whisper", lambda publish: self._faster_whisper_engine(publish)),
                ],
                replay_seconds=self.settings.asr_replay_seconds,
                health_check_seconds=self.settings.asr_health_check_seconds,
                startup_timeout_seconds=self.settings.session_start_timeout_seconds,
                on_provider_change=self._provider_changed,
            )
        raise ValueError(f"unsupported engine: {request.engine}")

    @staticmethod
    def _join_code(length: int = 6) -> str:
        return "".join(secrets.choice(_JOIN_ALPHABET) for _ in range(length))

    def _reset_metrics(self) -> None:
        self.metrics = RealtimeMetrics()
        self._stage_times.clear()
        self._vad.reset()
        self._refresh_queue_metrics()

    def _refresh_queue_metrics(self) -> None:
        if self.engine is not None:
            self.metrics.asr_provider = self.state.engine
            self.metrics.asr_running = self.engine.running
            self.metrics.asr_failure = self.engine.failure
            self.metrics.asr_failover_count = int(getattr(self.engine, "failover_count", 0))
            self.metrics.asr_last_failover_reason = getattr(
                self.engine,
                "last_failover_reason",
                None,
            )
            self.metrics.asr_last_failover_audio_ms = getattr(
                self.engine,
                "last_failover_audio_ms",
                None,
            )
            if not self._rust_session:
                self.metrics.asr_queue_depth = self.engine.queue_depth
                self.metrics.asr_queue_capacity = self.engine.queue_capacity
                self.metrics.asr_queue_high_watermark = max(
                    self.metrics.asr_queue_high_watermark,
                    self.engine.queue_depth,
                )
        else:
            self.metrics.asr_provider = None
            self.metrics.asr_running = False
            self.metrics.asr_failure = None
            self.metrics.asr_queue_depth = 0
            self.metrics.asr_queue_capacity = 0
        if not self._rust_session:
            self.metrics.persistence_queue_depth = self._persistence_queue.qsize()
            self.metrics.persistence_queue_capacity = self._persistence_queue.maxsize
            self.metrics.persistence_queue_high_watermark = max(
                self.metrics.persistence_queue_high_watermark,
                self._persistence_queue.qsize(),
            )
            self.metrics.postprocess_queue_depth = self.pipeline.queue_depth
            self.metrics.postprocess_queue_capacity = self.pipeline.queue_capacity
            self.metrics.postprocess_queue_lag_p50_ms = self.pipeline.queue_lag_p50_ms
            self.metrics.postprocess_queue_lag_p95_ms = self.pipeline.queue_lag_p95_ms
        self.metrics.websocket_clients = self.hub.client_count
        self.metrics.websocket_client_capacity = self.hub.max_clients
        self.metrics.websocket_clients_rejected = self.hub.clients_rejected
        self.metrics.websocket_overflow_evictions = self.hub.clients_evicted_overflow
        self.metrics.websocket_send_failures = self.hub.clients_evicted_send_failure
        self.metrics.websocket_client_queue_high_watermark = (
            self.hub.client_queue_high_watermark
        )
        if not self._rust_session:
            self.metrics.postprocess_queue_high_watermark = max(
                self.metrics.postprocess_queue_high_watermark,
                self.pipeline.queue_depth,
            )
            self.metrics.postprocess_segments_dropped = self.pipeline.dropped_postprocess_segments
            self.metrics.postprocess_segments_failed = self.pipeline.failed_postprocess_segments
            self.metrics.postprocess_segments_expired = self.pipeline.expired_postprocess_segments
            self.metrics.postprocess_duplicate_asr_events_dropped = (
                self.pipeline.duplicate_asr_events_dropped
            )
            self.metrics.postprocess_revised_asr_events_rejected = (
                self.pipeline.revised_asr_events_rejected
            )
            self.metrics.stale_asr_events_dropped = self.stale_asr_events_dropped
        if self._rust_session:
            self._apply_rust_metrics(
                self.engine.metrics_view() if self.engine is not None
                else self.metrics.rust_authority or RustAuthorityMetrics(status="not_sampled"),
            )
            if self.engine is not None and getattr(self.engine, "correction_status", None):
                self.state.correction_status = self.engine.correction_status
            if self.engine is not None and getattr(self.engine, "persistence_error", None):
                self.state.persistence_error = self.engine.persistence_error
        else:
            self.state.correction_status = self.pipeline.correction_status
            self.state.translation_status = self.pipeline.status

    def _apply_rust_metrics(self, view: RustAuthorityMetrics) -> None:
        self.metrics.runtime_core = "rust"
        self.metrics.rust_authority = view
        for name in (
            "asr_queue_depth", "asr_queue_capacity", "asr_queue_high_watermark",
            "persistence_queue_depth", "persistence_queue_capacity",
            "persistence_queue_high_watermark",
            "persistence_events_dropped", "persistence_events_failed", "postprocess_queue_depth",
            "postprocess_queue_capacity", "postprocess_queue_high_watermark",
            "postprocess_queue_lag_p50_ms", "postprocess_queue_lag_p95_ms",
            "postprocess_segments_dropped", "postprocess_segments_failed",
            "postprocess_segments_expired",
            "postprocess_duplicate_asr_events_dropped", "postprocess_revised_asr_events_rejected",
            "stale_asr_events_dropped",
        ):
            setattr(self.metrics, name, None)
        sample = view.sample
        if sample is None or view.status not in {"fresh", "final"}:
            return
        self.metrics.asr_queue_depth = sample.ingress.queue_depth
        self.metrics.asr_queue_capacity = sample.ingress.queue_capacity
        self.metrics.asr_queue_high_watermark = sample.ingress.queue_high_watermark
        queues = [queue for queue in (sample.correction, sample.translation) if queue is not None]
        self.metrics.postprocess_queue_depth = sum(queue.queued + queue.active for queue in queues)
        self.metrics.postprocess_queue_capacity = sum(queue.capacity + 1 for queue in queues)
        # Distinct job populations have no exact combined peak/percentile or segment count.
        if sample.history is not None:
            self.metrics.persistence_queue_depth = sample.history.queue_depth
            self.metrics.persistence_queue_capacity = sample.history.queue_capacity
            self.metrics.persistence_queue_high_watermark = sample.history.queue_high_watermark
            self.metrics.persistence_events_dropped = sample.history.dropped
            self.metrics.persistence_events_failed = sample.history.write_errors

    def metrics_snapshot(self) -> RealtimeMetrics:
        self._refresh_queue_metrics()
        return self.metrics.model_copy(deep=True)

    async def start(
        self, request: StartSessionRequest, *,
        before_transition: Callable[[], Awaitable[None]] | None = None,
    ) -> SessionState:
        async with self._lifecycle_lock:
            try:
                return await self._start_unlocked(request, before_transition=before_transition)
            except TimeoutError as exc:
                raise AsrEngineError("Session provider initialization timed out; retry") from exc

    async def _start_unlocked(
        self, request: StartSessionRequest, *,
        before_transition: Callable[[], Awaitable[None]] | None = None,
    ) -> SessionState:
        if self.settings.runtime_core == "rust":
            from .rust_runtime import validate_rust_request

            validate_rust_request(self.settings, request)
        await self._await_source_stop()
        if self._rust_slot.locked() and self.engine is None:
            raise AsrEngineError("Rust cleanup unconfirmed; restart the server before retrying")
        await self.initialize()
        # Validate before stopping a running session: an invalid project must not
        # interrupt a live event or create an unassigned session by accident.
        if request.project_id is not None and not await asyncio.to_thread(
            self.history.project_exists, request.project_id,
        ):
            raise ValueError("project not found")
        # Reject malformed provider IDs and unsupported directed language
        # pairs before tearing down a healthy session from another operator.
        # Provider readiness remains a separate recoverable preflight check.
        self.pipeline.validate_provider_configuration(request)
        engine = self._build_engine(request)
        generation = object()

        async def publish_from_this_session(event: TranscriptEvent) -> None:
            if self._rust_session and self._rust_relay_generation is generation:
                # Explicit Rust stop drains final events before revoking this relay;
                # the next session waits for that owned cleanup, as on Python stop.
                await self._publish(event)
                if "available" in event.translation_states.values():
                    self.state.translation_status.available = True
                return
            if self._active_generation is not generation:
                self.stale_asr_events_dropped += 1
                return
            await self.pipeline.ingest(
                event, is_current=lambda: self._active_generation is generation,
            )

        # All built-in AsrEngine implementations expose a writable publish
        # callback; ReplayFallbackAsrEngine forwards through self.publish.
        # Install the guard before starting any ASR/background tasks.
        engine.publish = publish_from_this_session
        if getattr(engine, "on_provider_change", None) is not None:
            async def guarded_provider_change(provider: str) -> None:
                if self._active_generation is generation:
                    await self._provider_changed(provider)

            engine.on_provider_change = guarded_provider_change

        if request.record_audio and not engine.accepts_audio:
            raise ValueError("recording requires a real audio-input ASR engine")
        await self._await_recording_finalizer()
        if before_transition is not None:
            await before_transition()
        if self.state.running:
            await self._stop_unlocked()
        self._rust_session = self.settings.runtime_core == "rust"
        self._rust_relay_generation = generation if self._rust_session else None
        self._active_generation = generation
        self.store.clear()
        await self.hub.close_all(reason="caption session starting")
        self._reset_metrics()
        start_deadline = (
            asyncio.get_running_loop().time() + self.settings.session_start_timeout_seconds
        )
        try:
            async with asyncio.timeout_at(start_deadline):
                if not self._rust_session:
                    await self.pipeline.start(request)
        except (Exception, asyncio.CancelledError):
            # A failed session never authorizes its ASR callbacks to publish.
            self._active_generation = None
            with suppress(Exception):
                await self.pipeline.stop()
            raise

        self.engine = engine
        self._audio_samples_received = 0
        self._recording_phase = "idle"
        self._recording_result = None
        self.state = SessionState(
            session_id=str(uuid4()),
            project_id=request.project_id,
            join_code=self._join_code(),
            running=False,
            source_language=request.source_language,
            target_languages=request.target_languages,
            engine=request.engine,
            resource_profile=(
                "low" if (
                    self.settings.resource_profile == "low" or request.resource_profile == "low"
                )
                else "standard"
            ),
            context=request.context,
            audio_required=engine.accepts_audio,
            audio_sample_rate=engine.sample_rate,
            recording_enabled=request.record_audio,
            collect_audience_metrics=request.collect_audience_metrics,
            recording_error=None,
            correction_status=(CorrectionStatus() if self._rust_session
                               else self.pipeline.correction_status),
            translation_status=(TranslationStatus(
                enabled=request.translation_provider != "none",
                provider=request.translation_provider,
            ) if self._rust_session else self.pipeline.status),
            persistence_error=None,
            started_at=datetime.now(UTC),
        )
        state = self.state
        creation_cancelled = ThreadEvent()
        creation_done = ThreadEvent()

        def persist_session() -> None:
            try:
                self.history.create_session(state, request)
            finally:
                creation_done.set()
                # A cancelled waiter cannot cancel SQLite work already running in a thread.
                if creation_cancelled.is_set() and state.session_id:
                    self.history.mark_ended(state.session_id)

        try:
            try:
                await asyncio.to_thread(persist_session)
            except Exception:
                self.state.persistence_error = _PERSISTENCE_FAILURE
            if self._rust_session:
                engine.session_id = state.session_id
            else:
                self._start_persistence_worker()
            try:
                async with asyncio.timeout_at(start_deadline):
                    await engine.start(request)
            except TimeoutError as exc:
                raise AsrEngineError(
                    "ASR initialization timed out; prepare the model and retry"
                ) from exc
            active_provider = getattr(engine, "active_provider", None)
            if active_provider:
                await self._provider_changed(str(active_provider))
            self.state.audio_sample_rate = engine.sample_rate
            if request.record_audio:
                # Recording is optional: storage failure must never prevent captions.
                if self.state.persistence_error is not None:
                    self.state.recording_error = "recording unavailable: session not persisted"
                else:
                    await self._open_recorder_unlocked(ThreadEvent())
            self.state.running = True
            self._refresh_queue_metrics()
        except (Exception, asyncio.CancelledError):
            self.state.running = False
            self._active_generation = None
            self._rust_relay_generation = None
            creation_cancelled.set()
            self.engine = None
            recorder, self._recorder = self._recorder, None
            if recorder is not None:
                with suppress(Exception):
                    await asyncio.shield(self._finalize_recorder(recorder, self.state))
            with suppress(Exception):
                await engine.stop()
            with suppress(Exception):
                await self.pipeline.stop()
            self._active_generation = None
            with suppress(Exception):
                await self._stop_persistence_worker()
            if (creation_done.is_set() and self.state.session_id
                    and self.state.persistence_error is None):
                try:
                    await asyncio.to_thread(self.history.mark_ended, self.state.session_id)
                except Exception:
                    self.state.persistence_error = _PERSISTENCE_FAILURE
            await self.hub.close_all(reason="caption session failed to start")
            raise
        return self.state

    async def stop(self, expected_session_id: str | None = None) -> SessionState:
        async with self._lifecycle_lock:
            if expected_session_id is not None and self.state.session_id != expected_session_id:
                raise SessionChangedError("session changed; refresh before stopping")
            return await self._stop_unlocked()

    async def _stop_unlocked(self) -> SessionState:
        if self._source_stop is not None and not self._source_stop.done():
            return await asyncio.shield(self._source_stop)
        state = self.state
        was_running = state.running
        session_id = state.session_id
        state.running = False
        # Revoke old ASR callbacks before awaiting engine.stop(): providers
        # can emit late callbacks while their shutdown is suspended.
        # Postprocessing drain still publishes through runtime._publish.
        self._active_generation = None
        engine, self.engine = self.engine, None
        recorder, self._recorder = self._recorder, None
        finalizer = (
            self._finalize_recorder(recorder, state)
            if recorder is not None else self._recording_finalizer
        )
        task = asyncio.create_task(
            self._finish_source_stop(state, was_running, session_id, engine, finalizer),
            name="caption-source-stop-owner",
        )
        self._source_stop = task
        def finished(done):
            if self._source_stop is done:
                self._source_stop = None
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _await_source_stop(self):
        if self._source_stop is not None and not self._source_stop.done():
            await asyncio.shield(self._source_stop)

    @property
    def source_cleanup_pending(self) -> bool:
        return self._source_stop is not None and not self._source_stop.done()

    @property
    def model_work_active(self) -> bool:
        return (self.state.running or self.engine is not None
                or self.source_cleanup_pending or self._rust_slot.locked())

    async def _finish_source_stop(
        self, state: SessionState, was_running: bool, session_id: str | None,
        engine: AsrEngine | None, finalizer: asyncio.Task | None,
    ) -> SessionState:
        # This whole source cleanup outlives HTTP cancellation, not just its WAV
        # finalizer. Every new source transition waits before reusing the pipeline.
        first_error: Exception | None = None
        if engine is not None:
            try:
                await engine.stop()
            except Exception as exc:
                first_error = exc
            if getattr(engine, "persistence_error", None):
                state.persistence_error = engine.persistence_error
            if self._rust_session:
                self._apply_rust_metrics(engine.metrics_view())
        self._rust_relay_generation = None

        try:
            await self.pipeline.stop()
        except Exception as exc:
            if first_error is None:
                first_error = exc
        try:
            await self._stop_persistence_worker()
        except Exception as exc:
            if first_error is None:
                first_error = exc

        if finalizer is not None:
            await asyncio.shield(finalizer)

        if was_running and session_id and not self._rust_session:
            try:
                await asyncio.to_thread(self.history.mark_ended, session_id)
            except Exception:
                state.persistence_error = _PERSISTENCE_FAILURE

        await self.hub.close_all(reason="caption session stopped")
        self.metrics.voice_active = False
        self._refresh_queue_metrics()
        if first_error is not None:
            raise first_error
        return state

    async def _await_recording_finalizer(self):
        if self._recording_finalizer is not None:
            await asyncio.shield(self._recording_finalizer)

    async def _open_recorder_unlocked(self, cancelled: ThreadEvent) -> None:
        state, engine = self.state, self.engine
        self._recording_phase = "starting"
        writer = SessionRecordingWriter(
            self.archive, session_id=state.session_id, sample_rate=engine.sample_rate,
            reserve_bytes=self.settings.storage_reserve_mb * 1024 * 1024,
            start_cancelled=cancelled,
        )
        try:
            await writer.start()
            if cancelled.is_set():
                await asyncio.shield(self._finalize_recorder(writer, state))
                return
            # No await between this PCM-clock snapshot and attachment. Opening a
            # file never stalls feed_audio, so samples received meanwhile are skipped.
            writer.start_offset_ms = self._audio_samples_received * 1000 / engine.sample_rate
            self._recorder = writer
            self._recording_phase = "recording"
        except asyncio.CancelledError:
            cancelled.set()
            self._recording_phase = "idle"
            raise
        except Exception:
            self._recording_phase = "error"
            state.recording_error = "recording unavailable: storage initialization failed"

    def _finalize_recorder(self, recorder, state):
        self._recording_phase = "stopping"

        async def finish():
            try:
                result = await recorder.stop()
                if self.state is state:
                    if result is not None:
                        self._recording_result = result
                    state.recording_error = recorder.error
            except Exception:
                if self.state is state:
                    state.recording_error = "recording finalization failed"
            finally:
                if self.state is state:
                    self._recording_phase = "error" if state.recording_error else "idle"

        task = asyncio.create_task(finish(), name="recording-finalizer")
        self._recording_finalizer = task
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return task

    def recording_status(self) -> dict:
        writer, state = self._recorder, self.state
        error = writer.error if writer is not None and writer.error else state.recording_error
        return {
            "session_id": state.session_id, "session_running": state.running,
            "available": bool(state.running and self.engine is not None
                              and self.engine.accepts_audio and state.persistence_error is None
                              and not self._rust_session),
            "phase": "error" if error and writer is not None else self._recording_phase,
            "active": writer is not None, "error": error,
            "start_offset_ms": writer.start_offset_ms if writer else None,
            "recording": self._recording_result,
        }

    async def control_recording(
        self, action: str, expected_session_id: str, *, cancelled: ThreadEvent | None = None,
    ) -> dict:
        if action not in {"start", "stop"}:
            raise ValueError("unsupported recording action")
        if self._rust_session and action == "start":
            raise ValueError("recording parity requires the Python runtime during Rust rollout")
        if self._recording_control is not None and not self._recording_control.done():
            raise SessionChangedError("recording transition in progress; refresh before retrying")
        cancelled = cancelled if cancelled is not None else ThreadEvent()

        async def own():
            async with self._lifecycle_lock:
                state = self.state
                if state.session_id != expected_session_id or not state.running:
                    raise SessionChangedError("session changed; refresh before recording")
                if action == "start":
                    if self._recorder is not None:
                        raise SessionChangedError("recording is already active")
                    if not self.recording_status()["available"]:
                        raise ValueError("recording requires a persisted audio-input session")
                    await self._await_recording_finalizer()
                    if cancelled.is_set():
                        return self.recording_status()
                    state.recording_enabled = True
                    state.recording_error = None
                    await self._open_recorder_unlocked(cancelled)
                else:
                    recorder, self._recorder = self._recorder, None
                    if recorder is not None:
                        await asyncio.shield(self._finalize_recorder(recorder, state))
                    else:
                        await self._await_recording_finalizer()
                return self.recording_status()

        task = asyncio.create_task(own(), name=f"recording-{action}-owner")
        self._recording_control = task
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            async with asyncio.timeout(self.settings.session_start_timeout_seconds):
                return await asyncio.shield(task)
        except (asyncio.CancelledError, TimeoutError):
            if action == "start":
                cancelled.set()
            raise

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        # Capture the active provider once. A concurrent stop intentionally clears
        # self.engine before awaiting provider shutdown; repeatedly dereferencing
        # self.engine here could otherwise turn that race into AttributeError.
        engine = self.engine
        if engine is None or not self.state.running or not engine.accepts_audio:
            raise RuntimeError("there is no active audio ASR session")

        dbfs, voice_active = self._vad.analyze(pcm_f32le)
        self._audio_samples_received += len(pcm_f32le) // 4
        self.metrics.audio_frames_received += 1
        self.metrics.audio_bytes_received += len(pcm_f32le)
        self.metrics.audio_duration_ms += round(
            (len(pcm_f32le) / 4 / engine.sample_rate) * 1000.0,
            3,
        )
        self.metrics.audio_rms_dbfs = dbfs
        self.metrics.voice_active = voice_active

        # Mirror accepted audio before awaiting ASR: a concurrent stop or provider
        # exception must not silently drop the last captured PCM chunk.
        recorder = self._recorder
        if recorder is not None:
            recorder.offer(pcm_f32le)
            if recorder.error is not None:
                self.state.recording_error = recorder.error

        started = time.perf_counter()
        try:
            await engine.feed_audio(pcm_f32le)
        finally:
            self._refresh_queue_metrics()
        enqueue_wait_ms = round((time.perf_counter() - started) * 1000.0, 1)
        self.metrics.last_audio_enqueue_wait_ms = enqueue_wait_ms
        if enqueue_wait_ms >= self.settings.audio_backpressure_warn_ms:
            self.metrics.audio_backpressure_events += 1

    async def end_audio(self) -> None:
        engine = self.engine
        if engine is not None and engine.accepts_audio:
            await engine.end_audio()
            self._refresh_queue_metrics()

    def audio_info(self) -> AudioStreamInfo:
        engine = self.engine
        if engine is None or not self.state.running:
            raise RuntimeError("there is no active session")
        return AudioStreamInfo(
            engine=self.state.engine,
            required=engine.accepts_audio,
            sample_rate=engine.sample_rate,
        )

    def audience_view(self, join_code: str) -> AudienceSessionView:
        context = self.state.context
        if (
            not self.state.running
            or not self.state.session_id
            or not self.state.join_code
            or context is None
            or not context.audience_access
            or not secrets.compare_digest(self.state.join_code, join_code.upper())
        ):
            raise KeyError("audience session not found")
        return AudienceSessionView(
            session_id=self.state.session_id,
            join_code=self.state.join_code,
            running=self.state.running,
            title=context.title,
            presenter=context.presenter,
            preset=context.preset,
            source_language=self.state.source_language,
            target_languages=self.state.target_languages,
            display_settings=context.display_settings,
            collect_audience_metrics=self.state.collect_audience_metrics,
            started_at=self.state.started_at,
        )
