import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asr import AsrEngineError
from .config import get_settings
from .exports import export_json, export_srt, export_txt, export_vtt
from .glossary_repository import GlossaryRepository
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    GlossaryEntry,
    GlossaryRecord,
    NetworkInfo,
    SessionDetail,
    SessionRecord,
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
    await runtime.initialize()
    yield
    await runtime.shutdown()


app = FastAPI(
    title=settings.app_name,
    version="0.7.0",
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


def _best_export_segments(segments: list[TranscriptEvent]) -> list[TranscriptEvent]:
    committed = [segment for segment in segments if segment.committed]
    return committed or segments


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


@app.get("/api/v1/history", response_model=list[SessionRecord])
async def session_history(request: Request, limit: int = 50) -> list[SessionRecord]:
    _require_operator(request)
    return await asyncio.to_thread(runtime.history.list_sessions, limit)


@app.get("/api/v1/history/{session_id}", response_model=SessionDetail)
async def history_session(request: Request, session_id: str) -> SessionDetail:
    _require_operator(request)
    session = await asyncio.to_thread(runtime.history.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get(
    "/api/v1/history/{session_id}/captions",
    response_model=list[TranscriptEvent],
)
async def history_captions(request: Request, session_id: str) -> list[TranscriptEvent]:
    _require_operator(request)
    session = await asyncio.to_thread(runtime.history.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return await asyncio.to_thread(runtime.history.segments, session_id)


@app.get("/api/v1/history/{session_id}/export")
async def history_export(
    request: Request,
    session_id: str,
    export_format: str = Query(default="srt", alias="format"),
    lang: str | None = None,
) -> Response:
    _require_operator(request)
    session = await asyncio.to_thread(runtime.history.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    segments = _best_export_segments(
        await asyncio.to_thread(runtime.history.segments, session_id)
    )
    normalized = export_format.lower()
    if normalized == "srt":
        content = export_srt(segments, lang)
        media_type = "application/x-subrip; charset=utf-8"
    elif normalized == "vtt":
        content = export_vtt(segments, lang)
        media_type = "text/vtt; charset=utf-8"
    elif normalized == "txt":
        content = export_txt(segments, lang)
        media_type = "text/plain; charset=utf-8"
    elif normalized == "json":
        content = export_json(session, segments)
        media_type = "application/json; charset=utf-8"
    else:
        raise HTTPException(status_code=400, detail="format must be srt, vtt, txt, or json")
    filename = f"langtextflow-{session_id}.{normalized}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/v1/history/{session_id}", status_code=204)
async def delete_history_session(request: Request, session_id: str) -> Response:
    _require_operator(request)
    if runtime.state.running and runtime.state.session_id == session_id:
        raise HTTPException(status_code=409, detail="cannot delete active session")
    deleted = await asyncio.to_thread(runtime.history.delete_session, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=204)


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
