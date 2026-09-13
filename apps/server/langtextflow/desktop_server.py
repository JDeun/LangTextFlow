from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


class DesktopBoundaryApp:
    """ASGI boundary that hides developer-only HTTP surfaces from LAN clients."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") == "http":
            path = str(scope.get("path") or "")
            client = scope.get("client")
            host = client[0] if isinstance(client, (tuple, list)) and client else None
            if path in {"/docs", "/redoc", "/openapi.json"}:
                from langtextflow.network import is_loopback_client

                if not is_loopback_client(host):
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 404,
                            "headers": [(b"content-length", b"0")],
                        }
                    )
                    await send({"type": "http.response.body", "body": b""})
                    return
        await self.app(scope, receive, send)


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

    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse

    from langtextflow.desktop_shutdown import request_desktop_shutdown
    from langtextflow.main import app
    from langtextflow.network import is_loopback_client

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

    @app.post("/api/v1/desktop/shutdown", include_in_schema=False, status_code=202)
    async def desktop_shutdown(request: Request) -> dict[str, str]:
        host = request.client.host if request.client else None
        if not is_loopback_client(host):
            raise HTTPException(status_code=403, detail="desktop shutdown is local-only")
        request_desktop_shutdown()
        return {"status": "shutting-down"}


async def _serve_desktop(app: Any, *, host: str, port: int) -> None:
    import uvicorn

    from langtextflow.desktop_shutdown import (
        desktop_shutdown_requested,
        reset_desktop_shutdown,
    )

    reset_desktop_shutdown()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        access_log=False,
        log_level="info",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="desktop-uvicorn")
    try:
        while not task.done():
            if desktop_shutdown_requested():
                server.should_exit = True
                break
            await asyncio.sleep(0.05)
        await task
    finally:
        server.should_exit = True
        if not task.done():
            await task


def main() -> None:
    web_root = _prepare_desktop_environment()

    from langtextflow.config import get_settings
    from langtextflow.main import app

    _install_public_routes(web_root)
    settings = get_settings()
    # LAN audience/projector access is intentional; operator APIs remain loopback-gated.
    asyncio.run(
        _serve_desktop(
            DesktopBoundaryApp(app),
            host="0.0.0.0",  # nosec B104
            port=settings.backend_port,
        )
    )


if __name__ == "__main__":
    main()
