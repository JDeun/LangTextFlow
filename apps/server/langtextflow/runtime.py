import asyncio
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from .asr import AsrEngine, MockStreamingAsrEngine, VibeVoiceStreamingAsrEngine
from .config import get_settings
from .hub import WebSocketHub
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from .pipeline import CaptionPipeline
from .session_repository import SessionRepository
from .store import CaptionStore

_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class CaptionRuntime:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.store = CaptionStore(max_segments=self.settings.max_segments)
        self.hub = WebSocketHub()
        self.state = SessionState()
        self.pipeline = CaptionPipeline(self._publish, self.settings)
        self.engine: AsrEngine | None = None
        self.history = SessionRepository(self.settings.database_path)
        self._persistence_queue: asyncio.Queue[
            tuple[str, TranscriptEvent] | None
        ] = asyncio.Queue(maxsize=1024)
        self._persistence_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        if self._persistence_task is not None:
            return
        await asyncio.to_thread(self.history.initialize)
        self._persistence_task = asyncio.create_task(
            self._persistence_worker(),
            name="session-persistence",
        )

    async def shutdown(self) -> None:
        await self.stop()
        if self._persistence_task is not None:
            await self._persistence_queue.put(None)
            await self._persistence_task
            self._persistence_task = None

    async def _publish(self, event: TranscriptEvent) -> None:
        self.store.apply(event)
        await self.hub.broadcast(event)
        session_id = self.state.session_id
        if not session_id:
            return
        try:
            self._persistence_queue.put_nowait((session_id, event))
        except asyncio.QueueFull:
            self.state.persistence_error = "transcript persistence queue is full"

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

    def _build_engine(self, request: StartSessionRequest) -> AsrEngine:
        if request.engine == "mock":
            return MockStreamingAsrEngine(self.pipeline.ingest)
        if request.engine == "vibevoice":
            return VibeVoiceStreamingAsrEngine(
                self.pipeline.ingest,
                base_url=self.settings.vibevoice_url,
                queue_chunks=self.settings.audio_queue_chunks,
                max_frame_bytes=self.settings.max_audio_frame_bytes,
            )
        raise ValueError(f"unsupported engine: {request.engine}")

    @staticmethod
    def _join_code(length: int = 6) -> str:
        return "".join(secrets.choice(_JOIN_ALPHABET) for _ in range(length))

    async def start(self, request: StartSessionRequest) -> SessionState:
        await self.initialize()
        if self.state.running:
            await self.stop()
        self.store.clear()
        await self.pipeline.start(request)
        engine = self._build_engine(request)
        try:
            await engine.start(request)
        except Exception:
            await self.pipeline.stop()
            raise
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
            translation_status=self.pipeline.status,
            persistence_error=None,
            started_at=datetime.now(UTC),
        )
        try:
            await asyncio.to_thread(self.history.create_session, self.state, request)
        except Exception as exc:
            self.state.persistence_error = str(exc)
        return self.state

    async def stop(self) -> SessionState:
        session_id = self.state.session_id
        if self.engine is not None:
            await self.engine.stop()
            self.engine = None
        await self.pipeline.stop()
        if self._persistence_task is not None:
            await self._persistence_queue.join()
        if session_id and self._persistence_task is not None:
            try:
                await asyncio.to_thread(self.history.mark_ended, session_id)
            except Exception as exc:
                self.state.persistence_error = str(exc)
        self.state.running = False
        return self.state

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self.engine is None or not self.state.running or not self.engine.accepts_audio:
            raise RuntimeError("there is no active audio ASR session")
        await self.engine.feed_audio(pcm_f32le)

    async def end_audio(self) -> None:
        if self.engine is not None and self.engine.accepts_audio:
            await self.engine.end_audio()

    def audio_info(self) -> AudioStreamInfo:
        if self.engine is None or not self.state.running:
            raise RuntimeError("there is no active session")
        return AudioStreamInfo(
            engine=self.state.engine,
            required=self.engine.accepts_audio,
            sample_rate=self.engine.sample_rate,
        )

    def audience_view(self, join_code: str) -> AudienceSessionView:
        context = self.state.context
        if (
            not self.state.session_id
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
            started_at=self.state.started_at,
        )
