from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asr import AsrEngineError
from .config import get_settings
from .glossary_repository import GlossaryRepository
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    GlossaryEntry,
    GlossaryRecord,
    NetworkInfo,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from .network import is_loopback_client, local_ipv4_addresses
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
    version="0.6.0",
    description="Realtime caption orchestration API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_operator(request: Request) -> None:
    host = request.client.host if request.client else None
    if not is_loopback_client(host):
        raise HTTPException(status_code=403, detail="operator API is local-only")


def _operator_websocket_allowed(websocket: WebSocket) -> bool:
    host = websocket.client.host if websocket.client else None
    return is_loopback_client(host)


def _with_saved_glossary(request: StartSessionRequest) -> StartSessionRequest:
    saved = glossary_repository.active_for(request.context.preset)
    merged: dict[str, GlossaryEntry] = {entry.term: entry for entry in saved}
    for entry in request.context.glossary:
        if entry.enabled and entry.applies_to(request.context.preset):
            merged[entry.term] = entry
    context = request.context.model_copy(update={"glossary": list(merged.values())})
    return request.model_copy(update={"context": context})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/network", response_model=NetworkInfo)
async def network_info(request: Request) -> NetworkInfo:
    _require_operator(request)
    return NetworkInfo(
        addresses=local_ipv4_addresses(),
        frontend_port=settings.frontend_port,
        backend_port=settings.backend_port,
    )


@app.get("/api/v1/state", response_model=SessionState)
async def get_state(request: Request) -> SessionState:
    _require_operator(request)
    return runtime.state


@app.get("/api/v1/captions", response_model=list[TranscriptEvent])
async def get_captions(request: Request) -> list[TranscriptEvent]:
    _require_operator(request)
    return runtime.store.snapshot()


@app.get("/api/v1/glossary", response_model=list[GlossaryRecord])
def list_glossary(request: Request) -> list[GlossaryRecord]:
    _require_operator(request)
    return glossary_repository.list()


@app.post("/api/v1/glossary", response_model=GlossaryRecord, status_code=201)
def create_glossary(request: Request, entry: GlossaryEntry) -> GlossaryRecord:
    _require_operator(request)
    return glossary_repository.create(entry)


@app.put("/api/v1/glossary/{record_id}", response_model=GlossaryRecord)
def update_glossary(
    record_id: str,
    request: Request,
    entry: GlossaryEntry,
) -> GlossaryRecord:
    _require_operator(request)
    record = glossary_repository.update(record_id, entry)
    if record is None:
        raise HTTPException(status_code=404, detail="glossary entry not found")
    return record


@app.delete("/api/v1/glossary/{record_id}", status_code=204)
def delete_glossary(record_id: str, request: Request) -> Response:
    _require_operator(request)
    if not glossary_repository.delete(record_id):
        raise HTTPException(status_code=404, detail="glossary entry not found")
    return Response(status_code=204)


@app.post("/api/v1/glossary/presets/church", response_model=list[GlossaryRecord])
def seed_church_glossary(request: Request) -> list[GlossaryRecord]:
    _require_operator(request)
    return glossary_repository.seed(CHURCH_GLOSSARY)


@app.post("/api/v1/session/start", response_model=SessionState)
async def start_session(request: Request, payload: StartSessionRequest) -> SessionState:
    _require_operator(request)
    try:
        return await runtime.start(_with_saved_glossary(payload))
    except AsrEngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/session/stop", response_model=SessionState)
async def stop_session(request: Request) -> SessionState:
    _require_operator(request)
    return await runtime.stop()


@app.get("/api/v1/audio/config", response_model=AudioStreamInfo)
async def audio_config(request: Request) -> AudioStreamInfo:
    _require_operator(request)
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
    if not _operator_websocket_allowed(websocket):
        await websocket.close(code=4403, reason="operator socket is local-only")
        return
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
    if not _operator_websocket_allowed(websocket):
        await websocket.close(code=4403, reason="operator audio socket is local-only")
        return
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
