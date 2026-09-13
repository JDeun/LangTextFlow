from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parents[3]


def _prepare_desktop_environment() -> None:
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

    web_root = _bundle_root() / "web-dist"
    if web_root.is_dir():
        os.environ.setdefault("LANGTEXTFLOW_PUBLIC_WEB_ROOT", str(web_root))

    # The packaged backend is the LAN-facing audience/projector web server too.
    os.environ.setdefault("LANGTEXTFLOW_FRONTEND_PORT", os.environ.get("LANGTEXTFLOW_BACKEND_PORT", "8000"))
    os.environ.setdefault("LANGTEXTFLOW_ENVIRONMENT", "desktop")


def main() -> None:
    _prepare_desktop_environment()

    import uvicorn

    from langtextflow.config import get_settings

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
