"""Windows-only model-free diagnostic build, preserving the original 30s exit gate."""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import psutil

from scripts import build_desktop_sidecar

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/frozen-prepare-exit"


def build() -> Path:
    original = subprocess.run

    def with_hook(command, **kwargs):
        if "PyInstaller" in command:
            command = [*command[:-1], "--runtime-hook",
                       str(ROOT / "scripts/frozen_prepare_diagnostic_hook.py"), command[-1]]
        return original(command, **kwargs)

    with patch.object(build_desktop_sidecar.subprocess, "run", with_hook):
        build_desktop_sidecar.main()
    return build_desktop_sidecar.BIN_DIR / "langtextflow-server-x86_64-pc-windows-msvc.exe"


def observe(pid: int, owned: dict[int, float]) -> list[dict]:
    results = []
    try:
        processes = [psutil.Process(pid), *psutil.Process(pid).children(recursive=True)]
    except psutil.Error:
        processes = []
    for process in processes[:64]:
        try:
            created = process.create_time()
            owned.setdefault(process.pid, created)
            results.append({"pid": process.pid, "ppid": process.ppid(),
                            "created": created, "name": process.name()})
        except psutil.Error:
            pass
    return results


def sanitize(text: str) -> str:
    # faulthandler includes filenames but never frame locals. Publish basenames only.
    return re.sub(r'File "([^"]+)"',
                  lambda match: 'File "' + re.split(r"[/\\]", match[1])[-1] + '"', text)


def alive(pid: int, created: float) -> bool:
    try:
        return psutil.Process(pid).create_time() == created
    except psutil.NoSuchProcess:
        return False


def case(binary: Path, root: Path, index: int, stdin_open: bool,
         torch_zero: bool, piped: bool) -> dict:
    raw_log = root / f"case-{index}.raw"
    environment = {**os.environ, "LTF_TEST_FROZEN_DIAGNOSTIC_LOG": str(raw_log),
                   "LANGTEXTFLOW_APP_DATA": str(root / f"case-{index}-app")}
    if torch_zero:
        environment["USE_TORCH"] = "0"
    read_fd, write_fd = os.pipe()
    started = time.monotonic()
    process = subprocess.Popen([str(binary), "--m2m100-prepare-worker"],
                               stdin=read_fd,
                               stdout=subprocess.PIPE if piped else subprocess.DEVNULL,
                               stderr=subprocess.PIPE if piped else subprocess.DEVNULL,
                               cwd=root, env=environment)
    os.close(read_fd)
    if not stdin_open:
        os.close(write_fd)
        write_fd = None
    owned = {}
    initial = []
    result = {"index": index, "stdin": "open_pipe" if stdin_open else "closed_pipe",
              "torch": "USE_TORCH=0" if torch_zero else "inherited",
              "stdio": "PIPE" if piped else "DEVNULL", "pid": process.pid,
              "timeout_seconds": 30, "timed_out": False}
    try:
        while process.poll() is None and time.monotonic() - started < 30:
            current = observe(process.pid, owned)
            if len(current) > len(initial):
                initial = current
            time.sleep(.1)
        remaining = max(.001, 30 - (time.monotonic() - started))
        try:
            stdout, stderr = process.communicate(timeout=remaining)
            result["stdout_bytes"] = len(stdout or b"")
            result["stderr_bytes"] = len(stderr or b"")
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            result["timeout_tree"] = observe(process.pid, owned)
        result["process_returncode_before_cleanup"] = process.poll()
    finally:
        result["observed_tree"] = initial
        cleanup_deadline = time.monotonic() + 10
        if process.poll() is None:
            try:
                killed = subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                        capture_output=True, timeout=5)
                result["taskkill_returncode"] = killed.returncode
            except subprocess.TimeoutExpired:
                result.setdefault("cleanup_errors", []).append("taskkill_timeout")
        # Only identities descended from our child are eligible for cleanup.
        for pid, created in reversed(list(owned.items())):
            try:
                child = psutil.Process(pid)
                if child.create_time() == created and child.is_running():
                    child.kill()
                    child.wait(timeout=max(.001, cleanup_deadline - time.monotonic()))
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                result.setdefault("cleanup_errors", []).append(type(exc).__name__)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if write_fd is not None:
            os.close(write_fd)
        result["returncode"] = process.returncode
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        result["owned_tree_reaped"] = not any(alive(pid, created)
                                               for pid, created in owned.items())
        if raw_log.exists():
            with raw_log.open("rb") as stream:
                data = stream.read(65537)
            (OUTPUT / f"case-{index}.log").write_text(
                sanitize(data[:65536].decode("utf-8", "replace")), encoding="utf-8")
            result["log_truncated"] = len(data) > 65536
        else:
            result["runtime_hook_log_missing"] = True
    return result


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("Windows diagnostic only")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "source.json").write_text(json.dumps({
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                              cwd=ROOT, text=True).strip(),
        "instrumented_test_build": True, "model_downloads": False,
    }, indent=2) + "\n")
    binary = build()
    with binary.open("rb") as stream:
        binary_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                   cwd=ROOT, text=True).strip(),
              "binary_sha256": binary_hash, "expected_cases": 8,
              "instrumented_test_build": True, "model_downloads": False, "cases": []}
    with tempfile.TemporaryDirectory(prefix="ltf-frozen-exit-diagnostic-") as directory:
        for index, flags in enumerate(itertools.product((False, True), repeat=3)):
            report["cases"].append(case(binary, Path(directory), index, *flags))
            (OUTPUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 1 if any(item["timed_out"] or item["returncode"] != 2
                    or item.get("stdout_bytes", 0) or item.get("stderr_bytes", 0)
                    or not item["owned_tree_reaped"] for item in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
