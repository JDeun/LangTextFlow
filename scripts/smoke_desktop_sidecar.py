"""Run the actual frozen backend with disposable storage and synthetic captions only."""
from __future__ import annotations

import hashlib
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


def smoke_native_m2m100(request, root: Path) -> None:
    """Manual opt-in only: real fixed weights, no external inference or speech input."""
    from langtextflow.m2m100_prepare_worker import FILES, MODEL_REVISION

    report = {
        "status": "failed", "field_qualified": False, "quality_benchmark": False,
        "model_revision": MODEL_REVISION, "frozen_prepare_and_repair": False,
        "synthetic_source_actual_native_translation": False,
        "source_sha": subprocess.run(["git", "rev-parse", "HEAD"],
                                     check=True, capture_output=True, text=True).stdout.strip(),
    }
    output = Path(__file__).resolve().parents[1] / "artifacts/native-m2m100-setup-smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    def prepared():
        job = json.loads(request("/api/v1/setup/m2m100/prepare", {}))
        deadline = time.monotonic() + 1230
        while time.monotonic() < deadline:
            job = json.loads(request(f"/api/v1/setup/jobs/{job['job_id']}"))
            if job["state"] in {"completed", "cancelled", "error"}:
                assert job["state"] == "completed", "native setup failed"
                break
            time.sleep(.25)
        else:
            raise RuntimeError("native setup deadline exceeded")
        cache = root / "models/m2m100-native"
        directory = cache / json.loads((cache / "current.json").read_text())
        hashes = {}
        for item in FILES:
            with (directory / item.relative).open("rb") as stream:
                hashes[item.relative] = hashlib.file_digest(stream, "sha256").hexdigest()
            assert hashes[item.relative] == item.sha256, "prepared cache hash mismatch"
        report["files_sha256"] = hashes
        return directory

    try:
        first = prepared()
        providers = json.loads(request("/api/v1/translation/providers"))
        assert next(item for item in providers if item["provider"] == "m2m100-ct2")["configured"]
        state = json.loads(request("/api/v1/session/start", {
            "engine": "mock", "source_language": "ko", "target_languages": ["en"],
            "correction_provider": "none", "translation_provider": "m2m100-ct2",
        }))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            captions = json.loads(request(f"/api/v1/audience/{state['join_code']}/captions"))
            if any(item.get("translations", {}).get("en", "").strip() for item in captions):
                report["synthetic_source_actual_native_translation"] = True
                break
            time.sleep(.2)
        else:
            raise RuntimeError("native translation did not produce a target")
        assert not json.loads(request("/api/v1/session/stop", {}))["running"]
        (first / "model/config.json").write_bytes(b"damaged")
        repaired = prepared()
        assert repaired != first
        report["frozen_prepare_and_repair"] = True
        report["status"] = "passed"
    finally:
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def smoke_auxiliary_command(
    binary: Path, root: Path, environment: dict[str, str], arguments: list[str], expected_code: int,
) -> None:
    helper = subprocess.Popen(
        [str(binary), *arguments],
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
            f"frozen auxiliary command did not exit: {arguments[0]} {stderr[-2000:]!r}"
        ) from exc
    if helper.returncode != expected_code or stdout or stderr:
        raise RuntimeError(
            f"frozen auxiliary command failed ({helper.returncode}): "
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
            "LANGTEXTFLOW_OLLAMA_URL": "http://127.0.0.1:11434",
        }
        # Match the interpreter flags checked by PyInstaller's multiprocessing hook.
        smoke_auxiliary_command(binary, root, environment, [
            "-B", "-S", "-I", "-c", "from multiprocessing.resource_tracker import main;main(0)",
        ], 0)
        # Invalid setup arguments must exit quietly without starting a server or downloading.
        smoke_auxiliary_command(binary, root, environment, ["--m2m100-worker"], 2)
        smoke_auxiliary_command(binary, root, {**environment, "USE_TORCH": "0"}, [
            "--m2m100-prepare-worker", "--check-runtime",
        ], 0)
        smoke_auxiliary_command(binary, root, environment, ["--m2m100-prepare-worker"], 2)
        smoke_auxiliary_command(binary, root, environment, [
            "--faster-whisper-prefetch-worker", "invalid-model", str(root / "invalid-cache"),
        ], 1)
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
                                   "summary_external_providers": ["openai-compatible"],
                                   "approved_transcript_id": None, "warnings": []}
                exported = json.loads(request(
                    f"/api/v1/history/{state['session_id']}/export?format=json",
                ))
                assert any(item["original_text"] for item in exported["segments"])
                if os.environ.get("LTF_M2M100_PREPARE_SMOKE") == "1":
                    smoke_native_m2m100(request, root)
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
        "Frozen backend smoke passed: worker dispatch, multiprocessing EOF, UI, captions, shutdown"
    )


if __name__ == "__main__":
    main()
