from __future__ import annotations

import os
import sys
from pathlib import Path


def _web_root() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
        return bundle_root / "web-dist"
    repository_root = Path(__file__).resolve().parents[3]
    return repository_root / "apps" / "web" / "dist"


def _prepare_desktop_environment() -> Path:
    app_data = os.environ.get("LANGTEXTFLOW_APP_DATA", "").strip()
    if app_data:
        root = Path(app_data).expanduser().resolve()
        data_dir = root / "data"
        cache_dir = root / "cache"
        model_cache_dir = cache_dir / "models"
        runtime_dir = root / "runtime"
        data_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)

        os.environ.setdefault("LANGTEXTFLOW_DATABASE_PATH", str(data_dir / "langtextflow.db"))
        os.environ.setdefault("LANGTEXTFLOW_MODEL_CACHE_DIR", str(model_cache_dir))
        os.environ.setdefault("LANGTEXTFLOW_MANAGED_RUNTIME_DIR", str(runtime_dir))
        os.environ.setdefault("HF_HOME", str(model_cache_dir / "huggingface"))

    backend_port = os.environ.get("LANGTEXTFLOW_BACKEND_PORT", "8000")
    os.environ.setdefault("LANGTEXTFLOW_FRONTEND_PORT", backend_port)
    os.environ.setdefault("LANGTEXTFLOW_ENVIRONMENT", "desktop")
    return _web_root()


def _install_public_routes(web_root: Path) -> None:
    if not (web_root / "index.html").is_file():
        raise RuntimeError(f"packaged web assets are missing: {web_root}")

    from fastapi import HTTPException, Request, Response
    from fastapi.responses import FileResponse

    from langtextflow.main import app
    from langtextflow.network import is_loopback_client

    @app.middleware("http")
    async def hide_desktop_developer_surfaces(request: Request, call_next):
        host = request.client.host if request.client else None
        developer_surface = request.url.path in {"/docs", "/redoc", "/openapi.json"}
        if developer_surface and not is_loopback_client(host):
            return Response(status_code=404)
        return await call_next(request)

    index = web_root / "index.html"
    assets = (web_root / "assets").resolve()

    @app.get("/audience/{join_code}", include_in_schema=False)
    async def audience_surface(join_code: str) -> FileResponse:
        del join_code
        return FileResponse(index)

    @app.get("/display/{join_code}", include_in_schema=False)
    async def display_surface(join_code: str) -> FileResponse:
        del join_code
        return FileResponse(index)

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    async def public_asset(asset_path: str) -> FileResponse:
        candidate = (assets / asset_path).resolve()
        try:
            candidate.relative_to(assets)
        except ValueError as exc:
            raise HTTPException(status_code=404) from exc
        if not candidate.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(candidate)

    audio_worklet = web_root / "audio-worklet.js"
    if audio_worklet.is_file():

        @app.get("/audio-worklet.js", include_in_schema=False)
        async def public_audio_worklet() -> FileResponse:
            return FileResponse(audio_worklet)


def main() -> None:
    web_root = _prepare_desktop_environment()

    import uvicorn

    from langtextflow.config import get_settings

    _install_public_routes(web_root)
    settings = get_settings()
    uvicorn.run(
        "langtextflow.main:app",
        host="0.0.0.0",
        port=settings.backend_port,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
