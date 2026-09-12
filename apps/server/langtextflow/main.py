from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asr import AsrEngineError
from .config import get_settings
from .glossary_repository import GlossaryRepository
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    GlossaryEntry,
    GlossaryRecord,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from .presets import CHURCH_GLOSSARY
from .runtime import CaptionRuntime

settings = get_settings()
runtime = CaptionRuntime()
glossary_repository = GlossaryRepository(settings.database_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    glossary_repository.initialize()
    yield
    await runtime.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.5.0",
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


@app.get("/api/v1/glossary", response_model=list[GlossaryRecord])
def list_glossary() -> list[GlossaryRecord]:
    return glossary_repository.list()


@app.post("/api/v1/glossary", response_model=GlossaryRecord, status_code=201)
def create_glossary(entry: GlossaryEntry) -> GlossaryRecord:
    return glossary_repository.create(entry)


@app.put("/api/v1/glossary/{record_id}", response_model=GlossaryRecord)
def update_glossary(record_id: str, entry: GlossaryEntry) -> GlossaryRecord:
    record = glossary_repository.update(record_id, entry)
    if record is None:
        raise HTTPException(status_code=404, detail="glossary entry not found")
    return record


@app.delete("/api/v1/glossary/{record_id}", status_code=204)
def delete_glossary(record_id: str) -> Response:
    if not glossary_repository.delete(record_id):
        raise HTTPException(status_code=404, detail="glossary entry not found")
    return Response(status_code=204)


@app.post("/api/v1/glossary/presets/church", response_model=list[GlossaryRecord])
def seed_church_glossary() -> list[GlossaryRecord]:
    return glossary_repository.seed(CHURCH_GLOSSARY)


@app.post("/api/v1/session/start", response_model=SessionState)
async def start_session(request: StartSessionRequest) -> SessionState:
    try:
        return await runtime.start(request)
    except AsrEngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/session/stop", response_model=SessionState)
async def stop_session() -> SessionState:
    return await runtime.stop()


@app.get("/api/v1/audio/config", response_model=AudioStreamInfo)
async def audio_config() -> AudioStreamInfo:
    try:
        return runtime.audio_info()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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


@app.websocket("/ws/audio")
async def audio_socket(websocket: WebSocket) -> None:
    try:
        info = runtime.audio_info()
    except RuntimeError:
        await websocket.close(code=4409, reason="no active audio session")
        return
    if not info.required:
        await websocket.close(code=4400, reason="active engine does not accept audio")
        return

    await websocket.accept()
    await websocket.send_json({"type": "audio_config", **info.model_dump(mode="json")})
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            frame = message.get("bytes")
            text = message.get("text")
            if frame is not None:
                await runtime.feed_audio(frame)
            elif text == "end":
                await runtime.end_audio()
                break
    except WebSocketDisconnect:
        pass
    except (AsrEngineError, ValueError) as exc:
        await websocket.close(code=1011, reason=str(exc)[:120])
