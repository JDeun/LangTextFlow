from __future__ import annotations

import os
from pathlib import Path


def _prepare_desktop_environment() -> None:
    app_data = os.environ.get("LANGTEXTFLOW_APP_DATA", "").strip()
    if not app_data:
        return

    root = Path(app_data).expanduser().resolve()
    data_dir = root / "data"
    cache_dir = root / "cache"
    runtime_dir = root / "runtime"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("LANGTEXTFLOW_DATABASE_PATH", str(data_dir / "langtextflow.db"))
    os.environ.setdefault("LANGTEXTFLOW_MODEL_CACHE_DIR", str(cache_dir / "models"))
    os.environ.setdefault("LANGTEXTFLOW_MANAGED_RUNTIME_DIR", str(runtime_dir))
    os.environ.setdefault("LANGTEXTFLOW_ENVIRONMENT", "desktop")


def main() -> None:
    _prepare_desktop_environment()

    import uvicorn

    from .config import get_settings

    settings = get_settings()
    uvicorn.run(
        "langtextflow.main:app",
        host="127.0.0.1",
        port=settings.backend_port,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
