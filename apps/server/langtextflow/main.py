import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from datetime import UTC, datetime
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .asr import AsrEngineError
from .audience_security import AudienceJoinRateLimiter
from .config import get_settings
from .diagnostics import build_diagnostics_bundle
from .exports import export_json, export_srt, export_txt, export_vtt
from .glossary_recommendations import GlossaryRecommendation, recommend_glossary_terms
from .glossary_repository import GlossaryRepository
from .glossary_transfer import (
    GlossaryImportRequest,
    GlossaryImportResult,
    GlossaryTransferFormat,
    export_glossary,
    glossary_export_media_type,
    parse_glossary_import,
)
from .mdns import MdnsPublisher
from .model_cache import CacheInventory, ModelCacheManager
from .model_setup import ModelSetupJob, ModelSetupManager, ModelSetupRequest
from .models import (
    AudienceSessionView,
    AudioStreamInfo,
    GlossaryEntry,
    GlossaryRecord,
    NetworkInfo,
    SessionDetail,
    SessionMetadataUpdate,
    SessionRecord,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from .network import (
    is_loopback_client,
    local_ipv4_addresses,
    operator_origin_allowed,
    websocket_origin_allowed,
)
from .preflight import SystemPreflight, run_preflight
from .presets import CHURCH_GLOSSARY
from .runtime import CaptionRuntime
from .runtime_install import (
    RuntimeKind,
    RuntimeProvisionJob,
    RuntimeProvisionManager,
    RuntimeStatus,
)
from .telemetry import RealtimeMetrics
from .vibevoice_lifecycle import VibeVoiceLifecycleManager, VibeVoiceLifecycleState

settings = get_settings()
model_cache_root = Path(settings.model_cache_dir).expanduser().resolve()
model_cache_root.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(model_cache_root / "huggingface"))
runtime = CaptionRuntime()
glossary_repository = GlossaryRepository(settings.database_path)
model_setup_manager = ModelSetupManager(settings)
model_cache_manager = ModelCacheManager(settings)
runtime_provision_manager = RuntimeProvisionManager(settings)
vibevoice_lifecycle = VibeVoiceLifecycleManager(settings)
mdns_publisher = MdnsPublisher(settings)
audience_join_limiter = AudienceJoinRateLimiter(
    max_failures=settings.audience_join_max_failures,
    window_seconds=settings.audience_join_window_seconds,
    block_seconds=settings.audience_join_block_seconds,
    max_clients=settings.audience_join_max_tracked_clients,
)
_audio_socket_guard = Lock()
_audio_transition_lock = asyncio.Lock()
_active_audio_socket: WebSocket | None = None
_audio_socket_accepting = True


def _claim_audio_socket(websocket: WebSocket) -> bool:
    global _active_audio_socket
    with _audio_socket_guard:
        if not _audio_socket_accepting or _active_audio_socket is not None:
            return False
        _active_audio_socket = websocket
        return True


def _release_audio_socket(websocket: WebSocket) -> None:
    global _active_audio_socket
    with _audio_socket_guard:
        if _active_audio_socket is websocket:
            _active_audio_socket = None


def _block_audio_socket_acceptance() -> WebSocket | None:
    global _active_audio_socket, _audio_socket_accepting
    with _audio_socket_guard:
        _audio_socket_accepting = False
        websocket = _active_audio_socket
        _active_audio_socket = None
        return websocket


def _allow_audio_socket_acceptance() -> None:
    global _audio_socket_accepting
    with _audio_socket_guard:
        _audio_socket_accepting = True


async def _close_audio_socket_for_transition(reason: str) -> None:
    websocket = _block_audio_socket_acceptance()
    if websocket is None:
        return
    with suppress(Exception):
        await asyncio.wait_for(websocket.close(code=1012, reason=reason), timeout=1.0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    glossary_repository.initialize()
    await runtime.initialize()
    await asyncio.to_thread(mdns_publisher.start)
    try:
        yield
    finally:
        await _close_audio_socket_for_transition("server shutting down")
        await runtime.shutdown()
        await vibevoice_lifecycle.shutdown()
        await model_setup_manager.shutdown()
        await runtime_provision_manager.shutdown()
        await asyncio.to_thread(mdns_publisher.stop)
        _allow_audio_socket_acceptance()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
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


@app.middleware("http")
async def reject_remote_operator_api_before_body_parse(request: Request, call_next):
    """Reject LAN access to operator REST APIs before request-body parsing.

    Audience read-only endpoints intentionally remain reachable on the LAN. All
    other /api/v1 routes are operator-only and must be rejected at the ASGI
    middleware boundary so an untrusted LAN peer cannot spend CPU/memory on
    Pydantic parsing before receiving a 403.
    """
    path = request.url.path
    is_audience_path = path.startswith("/api/v1/audience/")
    if path.startswith("/api/v1/") and not is_audience_path:
        host = request.client.host if request.client else None
        if not is_loopback_client(host):
            return Response(
                content='{"detail":"operator API is local-only"}',
                status_code=403,
                media_type="application/json",
            )
        if not _operator_request_origin_allowed(request):
            return Response(
                content='{"detail":"operator browser origin is not allowed"}',
                status_code=403,
                media_type="application/json",
            )
    return await call_next(request)


def _operator_request_origin_allowed(request: Request) -> bool:
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().casefold()
    if fetch_site == "cross-site":
        return False
    return operator_origin_allowed(
        request.headers.get("origin"),
        allowed_origins=settings.cors_origins,
    )


def _require_operator(request: Request) -> None:
    host = request.client.host if request.client else None
    if not is_loopback_client(host):
        raise HTTPException(status_code=403, detail="operator API is local-only")
    if not _operator_request_origin_allowed(request):
        raise HTTPException(status_code=403, detail="operator browser origin is not allowed")


def _websocket_origin_allowed(websocket: WebSocket) -> bool:
    return websocket_origin_allowed(
        websocket.headers.get("origin"),
        allowed_origins=settings.cors_origins,
        allowed_origin_regex=settings.cors_origin_regex,
    )


def _operator_websocket_allowed(websocket: WebSocket) -> bool:
    host = websocket.client.host if websocket.client else None
    return is_loopback_client(host) and operator_origin_allowed(
        websocket.headers.get("origin"),
        allowed_origins=settings.cors_origins,
    )


def _audience_rate_limit_error(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="too many invalid audience join-code attempts",
        headers={"Retry-After": str(retry_after)},
    )


def _authorize_audience(client_host: str | None, join_code: str) -> AudienceSessionView:
    retry_after = audience_join_limiter.retry_after(client_host)
    if retry_after is not None:
        raise _audience_rate_limit_error(retry_after)
    try:
        view = runtime.audience_view(join_code)
    except KeyError as exc:
        retry_after = audience_join_limiter.record_failure(client_host)
        if retry_after is not None:
            raise _audience_rate_limit_error(retry_after) from exc
        raise HTTPException(status_code=404, detail="audience session not found") from exc
    audience_join_limiter.record_success(client_host)
    return view


def _with_saved_glossary(request: StartSessionRequest) -> StartSessionRequest:
    saved = glossary_repository.active_for(request.context.preset)
    merged: dict[str, GlossaryEntry] = {entry.term: entry for entry in saved}
    for entry in request.context.glossary:
        if entry.enabled and entry.applies_to(request.context.preset):
            merged[entry.term] = entry
    context = request.context.model_copy(update={"glossary": list(merged.values())})
    return request.model_copy(update={"context": context})


def _best_export_segments(segments: list[TranscriptEvent]) -> list[TranscriptEvent]:
    # SessionRepository and CaptionStore already retain only the latest version per
    # segment. Filtering globally to committed rows would drop a valid stable tail
    # whenever an earlier segment had already committed.
    return segments


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


@app.get("/api/v1/network/mdns")
async def mdns_status(request: Request) -> dict[str, str | bool | None]:
    _require_operator(request)
    state = mdns_publisher.state
    return {
        "hostname": state.hostname,
        "ready": state.ready,
        "error": state.error,
    }


@app.get("/api/v1/preflight", response_model=SystemPreflight)
async def system_preflight(
    request: Request,
    engine: str = "auto",
    translation_provider: str = "ollama",
    translation_model: str | None = None,
) -> SystemPreflight:
    _require_operator(request)
    return await run_preflight(
        settings,
        translation_model,
        engine=engine,
        translation_provider=translation_provider,
    )


@app.get("/api/v1/diagnostics")
async def diagnostics_bundle(request: Request) -> Response:
    _require_operator(request)
    state = runtime.state
    translation_provider = (
        state.translation_status.provider if state.translation_status.enabled else "none"
    )
    translation_model = state.translation_status.model
    try:
        preflight: object = await run_preflight(
            settings,
            translation_model,
            engine=state.engine,
            translation_provider=translation_provider,
        )
    except Exception as exc:  # pragma: no cover - defensive support path
        preflight = {"error": str(exc)[:1000]}

    mdns_state = mdns_publisher.state
    bundle = build_diagnostics_bundle(
        version=__version__,
        settings=settings,
        state=state,
        metrics=runtime.metrics_snapshot(),
        preflight=preflight,
        mdns_state={
            "hostname": mdns_state.hostname,
            "ready": mdns_state.ready,
            "error": mdns_state.error,
        },
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Response(
        content=bundle,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="langtextflow-diagnostics-{stamp}.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/v1/setup/vibevoice", response_model=VibeVoiceLifecycleState)
async def vibevoice_lifecycle_status(request: Request) -> VibeVoiceLifecycleState:
    _require_operator(request)
    return await vibevoice_lifecycle.status()


@app.post("/api/v1/setup/vibevoice/start", response_model=VibeVoiceLifecycleState)
async def start_vibevoice_sidecar(request: Request) -> VibeVoiceLifecycleState:
    _require_operator(request)
    try:
        return await vibevoice_lifecycle.start()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/setup/vibevoice/stop", response_model=VibeVoiceLifecycleState)
async def stop_vibevoice_sidecar(request: Request) -> VibeVoiceLifecycleState:
    _require_operator(request)
    if runtime.state.running:
        raise HTTPException(
            status_code=409,
            detail="active caption session must be stopped before stopping VibeVoice",
        )
    return await vibevoice_lifecycle.stop()


@app.get("/api/v1/setup/jobs", response_model=list[ModelSetupJob])
async def model_setup_jobs(request: Request) -> list[ModelSetupJob]:
    _require_operator(request)
    return await model_setup_manager.list_jobs()


@app.get("/api/v1/setup/jobs/{job_id}", response_model=ModelSetupJob)
async def model_setup_job(request: Request, job_id: str) -> ModelSetupJob:
    _require_operator(request)
    job = await model_setup_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="model setup job not found")
    return job


@app.post("/api/v1/setup/ollama/pull", response_model=ModelSetupJob, status_code=202)
async def pull_ollama_model(request: Request, payload: ModelSetupRequest) -> ModelSetupJob:
    _require_operator(request)
    try:
        return await model_setup_manager.start_ollama_pull(payload.normalized_model())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/api/v1/setup/faster-whisper/prefetch",
    response_model=ModelSetupJob,
    status_code=202,
)
async def prefetch_faster_whisper_model(
    request: Request,
    payload: ModelSetupRequest,
) -> ModelSetupJob:
    _require_operator(request)
    try:
        return await model_setup_manager.start_faster_whisper_prefetch(
            payload.normalized_model()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/setup/jobs/{job_id}/cancel", response_model=ModelSetupJob)
async def cancel_model_setup_job(request: Request, job_id: str) -> ModelSetupJob:
    _require_operator(request)
    job = await model_setup_manager.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="model setup job not found")
    return job


@app.get("/api/v1/setup/runtimes", response_model=list[RuntimeStatus])
async def runtime_statuses(request: Request) -> list[RuntimeStatus]:
    _require_operator(request)
    return await runtime_provision_manager.statuses()


@app.post("/api/v1/setup/runtimes/{kind}", response_model=RuntimeProvisionJob, status_code=202)
async def provision_runtime(request: Request, kind: RuntimeKind) -> RuntimeProvisionJob:
    _require_operator(request)
    if runtime.state.running:
        raise HTTPException(status_code=409, detail="stop the active caption session before changing runtimes")
    return await runtime_provision_manager.start(kind)


@app.get("/api/v1/setup/runtime-jobs", response_model=list[RuntimeProvisionJob])
async def runtime_provision_jobs(request: Request) -> list[RuntimeProvisionJob]:
    _require_operator(request)
    return await runtime_provision_manager.list_jobs()


@app.get("/api/v1/setup/runtime-jobs/{job_id}", response_model=RuntimeProvisionJob)
async def runtime_provision_job(request: Request, job_id: str) -> RuntimeProvisionJob:
    _require_operator(request)
    job = await runtime_provision_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="runtime provision job not found")
    return job


@app.post("/api/v1/setup/runtime-jobs/{job_id}/cancel", response_model=RuntimeProvisionJob)
async def cancel_runtime_provision_job(request: Request, job_id: str) -> RuntimeProvisionJob:
    _require_operator(request)
    job = await runtime_provision_manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="runtime provision job not found")
    return job


@app.get("/api/v1/setup/cache", response_model=CacheInventory)
async def model_cache_inventory(request: Request) -> CacheInventory:
    _require_operator(request)
    return await model_cache_manager.inventory()


@app.delete("/api/v1/setup/cache/{area}", response_model=CacheInventory)
async def clear_model_cache(request: Request, area: str) -> CacheInventory:
    _require_operator(request)
    if runtime.state.running:
        raise HTTPException(status_code=409, detail="stop the active caption session before clearing model cache")
    try:
        return await model_cache_manager.clear(area)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/state", response_model=SessionState)
async def get_state(request: Request) -> SessionState:
    _require_operator(request)
    return runtime.state


@app.get("/api/v1/metrics", response_model=RealtimeMetrics)
async def realtime_metrics(request: Request) -> RealtimeMetrics:
    _require_operator(request)
    return runtime.metrics_snapshot()


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


@app.patch("/api/v1/history/{session_id}", response_model=SessionDetail)
async def update_history_session(
    request: Request,
    session_id: str,
    payload: SessionMetadataUpdate,
) -> SessionDetail:
    _require_operator(request)
    if runtime.state.running and runtime.state.session_id == session_id:
        raise HTTPException(status_code=409, detail="cannot edit active session metadata")
    updated = await asyncio.to_thread(
        runtime.history.update_metadata,
        session_id,
        title=payload.title,
        notes=payload.notes,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="session not found")
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


@app.get("/api/v1/glossary/recommendations", response_model=list[GlossaryRecommendation])
async def glossary_recommendations(
    request: Request,
    session_limit: int = Query(default=20, ge=1, le=100),
    limit: int = Query(default=30, ge=1, le=100),
    min_occurrences: int = Query(default=2, ge=2, le=50),
) -> list[GlossaryRecommendation]:
    _require_operator(request)
    return await asyncio.to_thread(
        recommend_glossary_terms, runtime.history, glossary_repository,
        session_limit=session_limit, limit=limit, min_occurrences=min_occurrences,
    )


@app.get("/api/v1/glossary/export")
def glossary_export(
    request: Request,
    export_format: Annotated[GlossaryTransferFormat, Query(alias="format")] = (
        GlossaryTransferFormat.JSON
    ),
) -> Response:
    _require_operator(request)
    content = export_glossary(glossary_repository.list(), export_format)
    filename = f"langtextflow-glossary.{export_format.value}"
    return Response(
        content=content,
        media_type=glossary_export_media_type(export_format),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/v1/glossary/import", response_model=GlossaryImportResult)
def glossary_import(request: Request, payload: GlossaryImportRequest) -> GlossaryImportResult:
    _require_operator(request)
    try:
        entries = parse_glossary_import(payload)
        created, updated, skipped = glossary_repository.import_entries(
            entries,
            conflict_policy=payload.conflict_policy.value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return GlossaryImportResult(
        total=len(entries),
        created=created,
        updated=updated,
        skipped=skipped,
    )


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
    async with _audio_transition_lock:
        await _close_audio_socket_for_transition("caption session restarting")
        try:
            return await runtime.start(_with_saved_glossary(payload))
        except AsrEngineError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            _allow_audio_socket_acceptance()


@app.post("/api/v1/session/stop", response_model=SessionState)
async def stop_session(request: Request) -> SessionState:
    _require_operator(request)
    async with _audio_transition_lock:
        await _close_audio_socket_for_transition("caption session stopping")
        try:
            return await runtime.stop()
        finally:
            _allow_audio_socket_acceptance()


@app.get("/api/v1/audio/config", response_model=AudioStreamInfo)
async def audio_config(request: Request) -> AudioStreamInfo:
    _require_operator(request)
    try:
        return runtime.audio_info()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/v1/audience/{join_code}", response_model=AudienceSessionView)
async def audience_session(request: Request, join_code: str) -> AudienceSessionView:
    host = request.client.host if request.client else None
    return _authorize_audience(host, join_code)


@app.get("/api/v1/audience/{join_code}/captions", response_model=list[TranscriptEvent])
async def audience_captions(request: Request, join_code: str) -> list[TranscriptEvent]:
    host = request.client.host if request.client else None
    _authorize_audience(host, join_code)
    return runtime.store.snapshot()


async def _caption_socket(websocket: WebSocket) -> None:
    if not await runtime.hub.connect(websocket):
        return
    try:
        await websocket.send_json(
            {
                "type": "snapshot",
                "segments": [item.model_dump(mode="json") for item in runtime.store.snapshot()],
            }
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            await websocket.close(code=4400, reason="caption socket is server-push only")
            break
    except WebSocketDisconnect:
        pass
    finally:
        runtime.hub.disconnect(websocket)


@app.websocket("/ws/captions")
async def caption_socket(websocket: WebSocket) -> None:
    if not _operator_websocket_allowed(websocket):
        await websocket.close(
            code=4403,
            reason="operator socket is local-only or origin is not allowed",
        )
        return
    await _caption_socket(websocket)


@app.websocket("/ws/audience/{join_code}")
async def audience_caption_socket(websocket: WebSocket, join_code: str) -> None:
    if not _websocket_origin_allowed(websocket):
        await websocket.close(code=4403, reason="websocket origin is not allowed")
        return
    host = websocket.client.host if websocket.client else None
    retry_after = audience_join_limiter.retry_after(host)
    if retry_after is not None:
        await websocket.close(code=4429, reason=f"retry after {retry_after}s")
        return
    try:
        runtime.audience_view(join_code)
    except KeyError:
        retry_after = audience_join_limiter.record_failure(host)
        if retry_after is not None:
            await websocket.close(code=4429, reason=f"retry after {retry_after}s")
        else:
            await websocket.close(code=4404, reason="audience session not found")
        return
    audience_join_limiter.record_success(host)
    await _caption_socket(websocket)


@app.websocket("/ws/audio")
async def audio_socket(websocket: WebSocket) -> None:
    if not _operator_websocket_allowed(websocket):
        await websocket.close(
            code=4403,
            reason="operator audio socket is local-only or origin is not allowed",
        )
        return
    try:
        info = runtime.audio_info()
    except RuntimeError:
        await websocket.close(code=4409, reason="no active audio session")
        return
    if not info.required:
        await websocket.close(code=4400, reason="active engine does not accept audio")
        return

    if not _claim_audio_socket(websocket):
        await websocket.close(code=4409, reason="another audio source is already connected")
        return

    try:
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
                    if len(frame) > settings.max_audio_frame_bytes:
                        await websocket.close(
                            code=1009,
                            reason="audio frame exceeds configured safety limit",
                        )
                        break
                    await runtime.feed_audio(frame)
                elif text == "end":
                    await runtime.end_audio()
                    break
                else:
                    await websocket.close(code=4400, reason="invalid audio control message")
                    break
        except WebSocketDisconnect:
            pass
        except (AsrEngineError, RuntimeError, ValueError) as exc:
            await websocket.close(code=1011, reason=str(exc)[:120])
    finally:
        _release_audio_socket(websocket)
