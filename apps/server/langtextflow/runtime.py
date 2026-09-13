import asyncio
import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .asr import (
    AsrEngine,
    FasterWhisperStreamingAsrEngine,
    MockStreamingAsrEngine,
    ReplayFallbackAsrEngine,
    VibeVoiceStreamingAsrEngine,
)
from .config import Settings, get_settings
from .hub import WebSocketHub
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    CaptionStage,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from .pipeline import CaptionPipeline
from .session_repository import SessionRepository
from .store import CaptionStore
from .telemetry import EnergyVad, RealtimeMetrics, latency_ms

_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class CaptionRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = CaptionStore(max_segments=self.settings.max_segments)
        self.hub = WebSocketHub()
        self.state = SessionState()
        self.pipeline = CaptionPipeline(self._publish, self.settings)
        self.engine: AsrEngine | None = None
        self.history = SessionRepository(self.settings.database_path)
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
        self._audio_io_lock = asyncio.Lock()
        self._audio_claim_lock = asyncio.Lock()
        self._audio_stream_session_id: str | None = None
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await asyncio.to_thread(self.history.initialize)
        await asyncio.to_thread(self.history.recover_interrupted_sessions)
        self._initialized = True

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
        await self._persistence_queue.join()
        await self._persistence_queue.put(None)
        await task
        self._persistence_task = None
        self._refresh_queue_metrics()

    async def shutdown(self) -> None:
        await self.stop()

    async def _publish(self, event: TranscriptEvent) -> None:
        self.store.apply(event)
        self._observe_event(event)
        await self.hub.broadcast(event)
        session_id = self.state.session_id
        if not session_id or self._persistence_task is None:
            self._refresh_queue_metrics()
            return
        try:
            self._persistence_queue.put_nowait((session_id, event))
        except asyncio.QueueFull:
            self.state.persistence_error = "transcript persistence queue is full"
        self._refresh_queue_metrics()

    def _observe_event(self, event: TranscriptEvent) -> None:
        self.metrics.last_event_at = event.emitted_at
        timings = self._stage_times.setdefault(event.segment_id, {})
        timings[event.stage] = event.emitted_at

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
                except Exception as exc:
                    if self.state.session_id == session_id:
                        self.state.persistence_error = str(exc)
            finally:
                self._persistence_queue.task_done()
                self._refresh_queue_metrics()

    def _vibevoice_engine(self, publish=None) -> VibeVoiceStreamingAsrEngine:
        return VibeVoiceStreamingAsrEngine(
            publish or self.pipeline.ingest,
            base_url=self.settings.vibevoice_url,
            queue_chunks=self.settings.audio_queue_chunks,
            max_frame_bytes=self.settings.max_audio_frame_bytes,
            enqueue_timeout_seconds=self.settings.audio_enqueue_timeout_seconds,
            shutdown_timeout_seconds=self.settings.audio_shutdown_timeout_seconds,
        )

    def _faster_whisper_engine(self, publish=None) -> FasterWhisperStreamingAsrEngine:
        return FasterWhisperStreamingAsrEngine(
            publish or self.pipeline.ingest,
            model=self.settings.faster_whisper_model,
            device=self.settings.faster_whisper_device,
            compute_type=self.settings.faster_whisper_compute_type,
            chunk_seconds=self.settings.faster_whisper_chunk_seconds,
            queue_chunks=self.settings.audio_queue_chunks,
            max_frame_bytes=self.settings.max_audio_frame_bytes,
            enqueue_timeout_seconds=self.settings.audio_enqueue_timeout_seconds,
            shutdown_timeout_seconds=self.settings.audio_shutdown_timeout_seconds,
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
            except Exception as exc:
                self.state.persistence_error = str(exc)
        self._refresh_queue_metrics()

    def _build_engine(self, request: StartSessionRequest) -> AsrEngine:
        if request.engine == "mock":
            return MockStreamingAsrEngine(self.pipeline.ingest)
        if request.engine == "vibevoice":
            return self._vibevoice_engine()
        if request.engine == "faster-whisper":
            return self._faster_whisper_engine()
        if request.engine == "auto":
            return ReplayFallbackAsrEngine(
                self.pipeline.ingest,
                [
                    ("vibevoice", lambda publish: self._vibevoice_engine(publish)),
                    ("faster-whisper", lambda publish: self._faster_whisper_engine(publish)),
                ],
                replay_seconds=self.settings.asr_replay_seconds,
                health_check_seconds=self.settings.asr_health_check_seconds,
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
        self.metrics.persistence_queue_depth = self._persistence_queue.qsize()
        self.metrics.persistence_queue_capacity = self._persistence_queue.maxsize
        self.metrics.postprocess_queue_depth = self.pipeline.queue_depth
        self.metrics.postprocess_queue_capacity = self.pipeline.queue_capacity
        self.state.correction_status = self.pipeline.correction_status
        self.state.translation_status = self.pipeline.status

    def metrics_snapshot(self) -> RealtimeMetrics:
        self._refresh_queue_metrics()
        return self.metrics.model_copy(deep=True)

    async def start(self, request: StartSessionRequest) -> SessionState:
        async with self._lifecycle_lock:
            return await self._start_unlocked(request)

    async def _start_unlocked(self, request: StartSessionRequest) -> SessionState:
        await self.initialize()
        if self.state.running:
            await self._stop_unlocked()
        self.store.clear()
        self._reset_metrics()
        await self.pipeline.start(request)
        engine = self._build_engine(request)
        self.engine = engine
        self.state = SessionState(
            session_id=str(uuid4()),
            join_code=self._join_code(),
            running=True,
            source_language=request.source_language,
            target_languages=request.target_languages,
            engine=request.engine,
            context=request.context,
            audio_required=engine.accepts_audio,
            audio_sample_rate=engine.sample_rate,
            correction_status=self.pipeline.correction_status,
            translation_status=self.pipeline.status,
            persistence_error=None,
            started_at=datetime.now(UTC),
        )
        try:
            await asyncio.to_thread(self.history.create_session, self.state, request)
        except Exception as exc:
            self.state.persistence_error = str(exc)
        self._start_persistence_worker()
        try:
            await engine.start(request)
            active_provider = getattr(engine, "active_provider", None)
            if active_provider:
                await self._provider_changed(str(active_provider))
            self.state.audio_sample_rate = engine.sample_rate
            self._refresh_queue_metrics()
        except Exception:
            self.state.running = False
            self.engine = None
            await self.pipeline.stop()
            await self._stop_persistence_worker()
            if self.state.session_id and self.state.persistence_error is None:
                await asyncio.to_thread(self.history.mark_ended, self.state.session_id)
            raise
        return self.state

    async def stop(self) -> SessionState:
        async with self._lifecycle_lock:
            return await self._stop_unlocked()

    async def _stop_unlocked(self) -> SessionState:
        was_running = self.state.running
        session_id = self.state.session_id
        async with self._audio_io_lock:
            if self.engine is not None:
                await self.engine.stop()
                self.engine = None
        await self.pipeline.stop()
        await self._stop_persistence_worker()
        if was_running and session_id:
            try:
                await asyncio.to_thread(self.history.mark_ended, session_id)
            except Exception as exc:
                self.state.persistence_error = str(exc)
        self.state.running = False
        self.metrics.voice_active = False
        self._refresh_queue_metrics()
        return self.state

    async def claim_audio_stream(self) -> str | None:
        """Lease the active session's single audio-producer slot.

        A stale socket from a previous session may coexist briefly with a new
        session. The session-id lease prevents it from feeding the replacement
        engine while still allowing the new session to claim its own producer.
        """

        async with self._audio_claim_lock:
            session_id = self.state.session_id
            engine = self.engine
            if (
                not session_id
                or not self.state.running
                or engine is None
                or not engine.accepts_audio
            ):
                return None
            if self._audio_stream_session_id == session_id:
                return None
            self._audio_stream_session_id = session_id
            return session_id

    async def release_audio_stream(self, session_id: str) -> None:
        async with self._audio_claim_lock:
            if self._audio_stream_session_id == session_id:
                self._audio_stream_session_id = None

    async def feed_audio(self, pcm_f32le: bytes, *, session_id: str | None = None) -> None:
        async with self._audio_io_lock:
            if session_id is not None and self.state.session_id != session_id:
                raise RuntimeError("audio stream belongs to a different session")
            engine = self.engine
            if engine is None or not self.state.running or not engine.accepts_audio:
                raise RuntimeError("there is no active audio ASR session")

            dbfs, voice_active = self._vad.analyze(pcm_f32le)
            self.metrics.audio_frames_received += 1
            self.metrics.audio_bytes_received += len(pcm_f32le)
            self.metrics.audio_duration_ms += round(
                (len(pcm_f32le) / 4 / engine.sample_rate) * 1000.0,
                3,
            )
            self.metrics.audio_rms_dbfs = dbfs
            self.metrics.voice_active = voice_active

            started = time.perf_counter()
            try:
                await engine.feed_audio(pcm_f32le)
            finally:
                self._refresh_queue_metrics()
            enqueue_wait_ms = round((time.perf_counter() - started) * 1000.0, 1)
            self.metrics.last_audio_enqueue_wait_ms = enqueue_wait_ms
            if enqueue_wait_ms >= self.settings.audio_backpressure_warn_ms:
                self.metrics.audio_backpressure_events += 1

    async def end_audio(self, *, session_id: str | None = None) -> None:
        async with self._audio_io_lock:
            if session_id is not None and self.state.session_id != session_id:
                raise RuntimeError("audio stream belongs to a different session")
            engine = self.engine
            if engine is not None and engine.accepts_audio:
                await engine.end_audio()
                self._refresh_queue_metrics()

    def audio_info(self, *, session_id: str | None = None) -> AudioStreamInfo:
        if session_id is not None and self.state.session_id != session_id:
            raise RuntimeError("audio stream belongs to a different session")
        if self.engine is None or not self.state.running:
            raise RuntimeError("there is no active session")
        return AudioStreamInfo(
            engine=self.state.engine,
            required=self.engine.accepts_audio,
            sample_rate=self.engine.sample_rate,
        )

    def audience_view(self, join_code: str) -> AudienceSessionView:
        if len(join_code) != 6:
            raise KeyError("audience session not found")
        normalized_join_code = join_code.upper()
        if any(character not in _JOIN_ALPHABET for character in normalized_join_code):
            raise KeyError("audience session not found")

        context = self.state.context
        if (
            not self.state.session_id
            or not self.state.join_code
            or context is None
            or not context.audience_access
            or not secrets.compare_digest(self.state.join_code, normalized_join_code)
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
            started_at=self.state.started_at,
        )
