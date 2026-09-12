import secrets
from datetime import UTC, datetime
from uuid import uuid4

from .asr import AsrEngine, MockStreamingAsrEngine
from .config import get_settings
from .hub import WebSocketHub
from .models import (
    AudienceSessionView,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from .store import CaptionStore

_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class CaptionRuntime:
    def __init__(self) -> None:
        settings = get_settings()
        self.store = CaptionStore(max_segments=settings.max_segments)
        self.hub = WebSocketHub()
        self.state = SessionState()
        self.engine: AsrEngine | None = None

    async def publish(self, event: TranscriptEvent) -> None:
        self.store.apply(event)
        await self.hub.broadcast(event)

    def _build_engine(self, request: StartSessionRequest) -> AsrEngine:
        if request.engine == "mock":
            return MockStreamingAsrEngine(self.publish)
        raise ValueError(f"unsupported engine: {request.engine}")

    @staticmethod
    def _join_code(length: int = 6) -> str:
        return "".join(secrets.choice(_JOIN_ALPHABET) for _ in range(length))

    async def start(self, request: StartSessionRequest) -> SessionState:
        if self.state.running:
            await self.stop()
        self.store.clear()
        self.engine = self._build_engine(request)
        self.state = SessionState(
            session_id=str(uuid4()),
            join_code=self._join_code(),
            running=True,
            source_language=request.source_language,
            target_languages=request.target_languages,
            engine=request.engine,
            context=request.context,
            started_at=datetime.now(UTC),
        )
        await self.engine.start(request)
        return self.state

    async def stop(self) -> SessionState:
        if self.engine is not None:
            await self.engine.stop()
            self.engine = None
        self.state.running = False
        return self.state

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
