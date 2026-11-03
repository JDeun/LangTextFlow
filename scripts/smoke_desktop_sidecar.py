"""Run the actual frozen backend with disposable storage and synthetic captions only."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from build_desktop_sidecar import BIN_DIR, _target_triple


def smoke_multiprocessing_helper(binary: Path, root: Path, environment: dict[str, str]) -> None:
    # Match the frozen interpreter flags required by PyInstaller's diversion hook.
    helper = subprocess.Popen(
        [str(binary), "-B", "-S", "-I", "-c",
         "from multiprocessing.resource_tracker import main;main(0)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=root, env=environment, start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = helper.communicate(b"", timeout=30)
    except subprocess.TimeoutExpired as exc:
        # A regression must not orphan a bootloader's accidentally started server.
        if os.name == "posix":
            with suppress(ProcessLookupError):
                os.killpg(helper.pid, signal.SIGKILL)
        else:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(helper.pid)],
                capture_output=True, check=False,
            )
        helper.kill()
        stdout, stderr = helper.communicate(timeout=5)
        raise RuntimeError(
            f"frozen multiprocessing helper did not exit at EOF: {stderr[-2000:]!r}"
        ) from exc
    if helper.returncode != 0 or stdout or stderr:
        raise RuntimeError(
            f"frozen multiprocessing helper failed ({helper.returncode}): "
            f"stdout={stdout[-2000:]!r}, stderr={stderr[-2000:]!r}"
        )


def main() -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    binary = BIN_DIR / f"langtextflow-server-{_target_triple()}{suffix}"
    if not binary.is_file():
        raise RuntimeError(f"build the desktop sidecar first: {binary}")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    def request(path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else None
        with urlopen(Request(
            base + path, data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
        ), timeout=5) as response:
            return response.read()

    with tempfile.TemporaryDirectory(prefix="ltf-sidecar-smoke-") as directory:
        root = Path(directory)
        environment = {
            **os.environ,
            "LANGTEXTFLOW_APP_DATA": str(root),
            "LANGTEXTFLOW_DATABASE_PATH": str(root / "data" / "smoke.db"),
            "LANGTEXTFLOW_MODEL_CACHE_DIR": str(root / "models"),
            "LANGTEXTFLOW_MANAGED_RUNTIME_DIR": str(root / "runtime"),
            "LANGTEXTFLOW_BACKEND_PORT": str(port),
            "LANGTEXTFLOW_MDNS_ENABLED": "false",
            "HF_HOME": str(root / "models" / "huggingface"),
        }
        smoke_multiprocessing_helper(binary, root, environment)
        with (root / "sidecar.log").open("wb") as log:
            process = subprocess.Popen(
                [str(binary)], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                env=environment,
            )
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("packaged backend exited during startup")
                    try:
                        assert json.loads(request("/health")) == {"status": "ok"}
                        break
                    except URLError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("packaged backend startup timed out")
                state = json.loads(request("/api/v1/session/start", {
                    "engine": "mock", "source_language": "ko", "target_languages": ["en"],
                    "correction_provider": "none", "translation_provider": "none",
                }))
                assert state["running"] and state["session_id"] and state["join_code"]
                assert b"LangTextFlow" in request(f"/audience/{state['join_code']}")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    captions = json.loads(request(
                        f"/api/v1/audience/{state['join_code']}/captions",
                    ))
                    if any(item["committed"] for item in captions):
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("packaged mock ASR did not commit a caption")
                assert not json.loads(request("/api/v1/session/stop", {}))["running"]
                archive = json.loads(request(f"/api/v1/history/{state['session_id']}/archive"))
                assert archive == {"recordings": [], "summary": None, "summary_stale": False,
                                   "approved_transcript_id": None, "warnings": []}
                exported = json.loads(request(
                    f"/api/v1/history/{state['session_id']}/export?format=json",
                ))
                assert any(item["original_text"] for item in exported["segments"])
                request("/api/v1/desktop/shutdown", {})
                assert process.wait(timeout=15) == 0
            except BaseException:
                print((root / "sidecar.log").read_text(errors="replace")[-8000:], file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    print(
        "Frozen backend smoke passed: multiprocessing EOF, startup, UI, captions, archive, shutdown"
    )


if __name__ == "__main__":
    main()
