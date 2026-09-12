from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import AudienceSessionView, SessionState, StartSessionRequest, TranscriptEvent
from .runtime import CaptionRuntime

settings = get_settings()
runtime = CaptionRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await runtime.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Realtime caption orchestration API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/state", response_model=SessionState)
async def get_state() -> SessionState:
    return runtime.state


@app.get("/api/v1/captions", response_model=list[TranscriptEvent])
async def get_captions() -> list[TranscriptEvent]:
    return runtime.store.snapshot()


@app.post("/api/v1/session/start", response_model=SessionState)
async def start_session(request: StartSessionRequest) -> SessionState:
    try:
        return await runtime.start(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/session/stop", response_model=SessionState)
async def stop_session() -> SessionState:
    return await runtime.stop()


@app.get("/api/v1/audience/{join_code}", response_model=AudienceSessionView)
async def audience_session(join_code: str) -> AudienceSessionView:
    try:
        return runtime.audience_view(join_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audience session not found") from exc


@app.get("/api/v1/audience/{join_code}/captions", response_model=list[TranscriptEvent])
async def audience_captions(join_code: str) -> list[TranscriptEvent]:
    try:
        runtime.audience_view(join_code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="audience session not found") from exc
    return runtime.store.snapshot()


async def _caption_socket(websocket: WebSocket) -> None:
    await runtime.hub.connect(websocket)
    try:
        await websocket.send_json(
            {
                "type": "snapshot",
                "segments": [item.model_dump(mode="json") for item in runtime.store.snapshot()],
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.hub.disconnect(websocket)
    except Exception:
        runtime.hub.disconnect(websocket)
        raise


@app.websocket("/ws/captions")
async def caption_socket(websocket: WebSocket) -> None:
    await _caption_socket(websocket)


@app.websocket("/ws/audience/{join_code}")
async def audience_caption_socket(websocket: WebSocket, join_code: str) -> None:
    try:
        runtime.audience_view(join_code)
    except KeyError:
        await websocket.close(code=4404, reason="audience session not found")
        return
    await _caption_socket(websocket)
