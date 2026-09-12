from datetime import UTC, datetime

from .asr import AsrEngine, MockStreamingAsrEngine
from .config import get_settings
from .hub import WebSocketHub
from .models import SessionState, StartSessionRequest, TranscriptEvent
from .store import CaptionStore


class CaptionRuntime:
    def __init__(self) -> None:
        settings = get_settings()
        self.store = CaptionStore(max_segments=settings.max_segments)
        self.hub = WebSocketHub()
        self.state = SessionState()
        self.engine: AsrEngine = MockStreamingAsrEngine(self.publish)

    async def publish(self, event: TranscriptEvent) -> None:
        self.store.apply(event)
        await self.hub.broadcast(event)

    async def start(self, request: StartSessionRequest) -> SessionState:
        if request.engine != "mock":
            raise ValueError(f"unsupported engine in P0: {request.engine}")
        if self.state.running:
            await self.stop()
        self.store.clear()
        self.state = SessionState(
            running=True,
            source_language=request.source_language,
            target_languages=request.target_languages,
            engine=request.engine,
            started_at=datetime.now(UTC),
        )
        await self.engine.start(request)
        return self.state

    async def stop(self) -> SessionState:
        await self.engine.stop()
        self.state.running = False
        return self.state
