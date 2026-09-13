from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = ROOT / "apps" / "desktop" / "src-tauri" / "binaries"
ENTRYPOINT = ROOT / "apps" / "server" / "langtextflow" / "desktop_server.py"


def _target_triple() -> str:
    configured = os.environ.get("TAURI_TARGET_TRIPLE", "").strip()
    if configured:
        return configured
    result = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()
    raise RuntimeError("unable to determine Rust target triple")


def _collect_args(*packages: str) -> list[str]:
    args: list[str] = []
    for package in packages:
        if importlib.util.find_spec(package) is None:
            continue
        args.extend(["--collect-all", package])
    return args


def main() -> None:
    triple = _target_triple()
    name = "langtextflow-server"
    dist_dir = ROOT / "build" / "desktop-sidecar" / "dist"
    work_dir = ROOT / "build" / "desktop-sidecar" / "work"
    spec_dir = ROOT / "build" / "desktop-sidecar" / "spec"
    shutil.rmtree(dist_dir.parent, ignore_errors=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--paths",
        str(ROOT / "apps" / "server"),
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "langtextflow",
        *_collect_args("faster_whisper", "ctranslate2", "tokenizers", "numpy"),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        str(ENTRYPOINT),
    ]
    subprocess.run(command, check=True, cwd=ROOT)

    suffix = ".exe" if sys.platform == "win32" else ""
    built = dist_dir / f"{name}{suffix}"
    if not built.is_file():
        raise RuntimeError(f"desktop sidecar was not produced: {built}")

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = BIN_DIR / f"{name}-{triple}{suffix}"
    shutil.copy2(built, target)
    print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
