"""External frozen Rust/worker smoke with disposable data and no valid model.

Run explicitly after packaging. The fake-contract unit tests do not launch the
binary. A passed receipt proves idle wiring/ownership, not native recognition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

FRAME_LIMIT = 128 * 1024
IDENTITY = ("protocol_version", "request_id", "session_id", "session_generation",
            "segment_id", "source_revision")


class SmokeFailure(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SmokeFailure("loopback_redirect_forbidden")


def require(condition: bool, category: str) -> None:
    if not condition:
        raise SmokeFailure(category)


def process_api():
    import psutil

    # Fail before spawning a frozen process when the OS cannot observe ownership.
    own = psutil.Process()
    own.create_time()
    own.cmdline()
    own.children(recursive=True)
    own.net_connections(kind="inet")
    return psutil


class Owned:
    """Track only this Popen and observed descendants, including PID reuse fences."""

    def __init__(self, process, api):
        self.process, self.api = process, api
        self.seen: dict[int, dict] = {}
        self.observations = 0

    def known_parent_exited(self) -> bool:
        return (self.observations > 0 and self.process.pid in self.seen
                and self.process.poll() is not None)

    def snapshot(self) -> list[dict]:
        if self.process.poll() is not None:
            require(self.known_parent_exited(), "parent_unobserved")
            return []
        try:
            root = self.api.Process(self.process.pid)
            nodes = [root, *root.children(recursive=True)]
            current = []
            for node in nodes:
                try:
                    item = {"pid": node.pid, "created": node.create_time(),
                            "ppid": node.ppid(), "exe": node.exe(), "args": node.cmdline()}
                except self.api.NoSuchProcess:
                    continue  # Confirmed disappearance, never an AccessDenied success.
                previous = self.seen.get(node.pid)
                require(previous is None or previous["created"] == item["created"], "pid_reused")
                self.seen[node.pid] = item
                current.append(item)
            if not any(row["pid"] == self.process.pid for row in current):
                require(self.known_parent_exited(), "parent_unobserved")
                return current
            self.observations += 1
            return current
        except self.api.NoSuchProcess as exc:
            if self.known_parent_exited():
                return []
            raise SmokeFailure("parent_unobserved") from exc
        except (self.api.Error, OSError) as exc:
            raise SmokeFailure("process_observation_unavailable") from exc

    def alive(self, identities: list[dict] | None = None) -> list[dict]:
        result = []
        for item in self.seen.values() if identities is None else identities:
            try:
                process = self.api.Process(item["pid"])
                if process.create_time() == item["created"] and process.is_running():
                    result.append(item)
            except self.api.NoSuchProcess:
                pass
            except (self.api.Error, OSError) as exc:
                raise SmokeFailure("process_exit_observation_unavailable") from exc
        return result

    def gone(self, identities: list[dict], seconds: float = 10) -> None:
        require(bool(identities), "empty_ownership_proof")
        deadline = time.monotonic() + seconds
        while self.alive(identities):
            require(time.monotonic() < deadline, "owned_process_not_reaped")
            time.sleep(.05)

    def listener_owned(self, port: int) -> None:
        owned = False
        try:
            for item in self.snapshot():
                node = self.api.Process(item["pid"])
                require(node.create_time() == item["created"], "pid_reused")
                for connection in node.net_connections(kind="inet"):
                    if connection.status == "LISTEN" and connection.laddr.port == port:
                        owned = True
        except (self.api.Error, OSError) as exc:
            raise SmokeFailure("port_ownership_unobservable") from exc
        require(owned, "port_not_owned_by_backend")

    def cleanup(self) -> None:
        failure = None
        if self.process.poll() is None:
            try:
                self.snapshot()
            except SmokeFailure as exc:
                failure = exc
            # The original unreaped Popen child owns this newly-created POSIX
            # group. Do not require an unsafe lookup by PID after parent reap.
            if os.name == "posix":
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.terminate()  # Uses the original Windows process handle.
        try:
            live = self.alive()
            for item in reversed(live):
                try:
                    node = self.api.Process(item["pid"])
                    if node.create_time() == item["created"]:
                        node.kill()
                except self.api.NoSuchProcess:
                    pass
        except SmokeFailure as exc:
            failure = failure or exc
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        try:
            self.gone(list(self.seen.values()))
        except SmokeFailure as exc:
            failure = failure or exc
        if failure:
            raise failure


def request_frame(task: str, payload: dict, serial: int = 1) -> dict:
    return {"protocol_version": 1, "request_id": f"smoke-{serial}",
            "session_id": "frozen-smoke", "session_generation": 1,
            "segment_id": f"segment-{serial}", "source_revision": 1,
            "task_kind": task, "source_language": "ko",
            "target_language": "en" if task == "translation" else None,
            "deadline_ms": 10_000, "payload": json.dumps(payload, ensure_ascii=False)}


def validate_reply(raw: bytes, request: dict, result: str | None, error: str | None) -> dict:
    require(len(raw) <= FRAME_LIMIT and raw.endswith(b"\n"), "invalid_worker_frame_size")
    reply = json.loads(raw.decode("utf-8"))
    require(isinstance(reply, dict) and set(reply) == {*IDENTITY, "status", "result", "error"},
            "unexpected_worker_fields")
    require(all(type(reply[key]) is type(request[key]) and reply[key] == request[key]
                for key in IDENTITY), "worker_identity_mismatch")
    require(reply["status"] == ("error" if error else "ok")
            and reply["result"] == result and reply["error"] == error, "unexpected_worker_result")
    return reply


def exchange(owned: Owned, request: dict) -> bytes:
    replies: queue.Queue = queue.Queue(maxsize=1)

    def read_frame():
        pending = bytearray()
        try:
            while len(pending) <= FRAME_LIMIT:
                chunk = os.read(owned.process.stdout.fileno(), 4096)
                if not chunk:
                    break
                pending.extend(chunk)
                if b"\n" in pending:
                    break
            replies.put_nowait(bytes(pending))
        except OSError:
            replies.put_nowait(b"")

    frame = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    require(len(frame) < 4096, "smoke_request_exceeds_one_small_pipe_write")
    reader = threading.Thread(target=read_frame, daemon=True)
    reader.start()
    owned.process.stdin.write(frame)
    owned.process.stdin.flush()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        owned.snapshot()
        try:
            return replies.get(timeout=.05)
        except queue.Empty:
            continue
    raise SmokeFailure("worker_reply_deadline")


def direct_asr(rows: list[dict]) -> tuple[dict, dict]:
    runtimes = [row for row in rows
                if Path(row["exe"]).name in {"langtextflow-runtime", "langtextflow-runtime.exe"}]
    workers = [row for row in rows if "--asr-inference-worker" in row["args"]]
    require(len(runtimes) == len(workers) == 1, "rust_asr_process_count_or_inner_bootloader")
    runtime, worker = runtimes[0], workers[0]
    require(worker["ppid"] == runtime["pid"], "asr_not_direct_rust_child")
    require(not any(row["ppid"] == worker["pid"] for row in rows), "asr_has_inner_process")
    path = Path(runtime["exe"])
    require(path.parent.name == "runtime" and any(part.startswith("_MEI") for part in path.parts),
            "runtime_not_resolved_from_frozen_bundle")
    return runtime, worker


def recorded_direct_asr(owned: Owned, graph_log) -> tuple[dict, dict]:
    rows = owned.snapshot()
    flags = {"--asr-inference-worker", "--correction-worker", "--m2m100-worker",
             "--rust-protocol"}
    graph = []
    for row in rows:
        path = Path(row["exe"])
        graph.append({"pid": row["pid"], "ppid": row["ppid"], "created": row["created"],
                      "exe_basename": path.name,
                      "fixed_worker_flags": sorted(flags.intersection(row["args"])),
                      "runtime_from_bundle": path.name in {
                          "langtextflow-runtime", "langtextflow-runtime.exe"}
                      and path.parent.name == "runtime"
                      and any(part.startswith("_MEI") for part in path.parts)})
    graph_log.write(json.dumps({"processes": graph}) + "\n")
    graph_log.flush()  # Preserve the failing graph before any ownership assertion.
    return direct_asr(rows)


def environment(root: Path) -> dict[str, str]:
    # The fixture cannot inherit operator/cloud/model settings from the caller.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("LANGTEXTFLOW_", "HF_", "HUGGINGFACE_", "_PYI_"))}
    env.update({
        "LANGTEXTFLOW_APP_DATA": str(root),
        "LANGTEXTFLOW_DATABASE_PATH": str(root / "smoke.sqlite"),
        "LANGTEXTFLOW_MODEL_CACHE_DIR": str(root / "cache"),
        "LANGTEXTFLOW_MANAGED_RUNTIME_DIR": str(root / "managed-runtime"),
        "LANGTEXTFLOW_RUNTIME_CORE": "rust", "LANGTEXTFLOW_RUST_RUNTIME_BINARY": "",
        "LANGTEXTFLOW_MDNS_ENABLED": "false", "LANGTEXTFLOW_METRICS_ENABLED": "false",
        "LANGTEXTFLOW_OLLAMA_URL": "http://127.0.0.1:1",
        "HF_HOME": str(root / "hf"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "USE_TORCH": "0", "PYINSTALLER_RESET_ENVIRONMENT": "1",
    })
    return env


def worker_probe(binary: Path, root: Path, env: dict, api, kind: str) -> dict:
    text = "성령 🙏 日本語"
    payload = {"mode": "deterministic", "text": text, "context": {"title": "Frozen smoke"},
               "options": {"provider": "none", "url": "", "model": "unused", "timeout_seconds": 1}}
    if kind == "asr":
        arguments = ["--asr-inference-worker", str(root / "fake-whisper"), str(root / "hf"), "2"]
        bad = root / "invalid.wav"
        bad.write_bytes(b"not a WAV")
        payload = {"wav_path": str(bad), "start_sample": 0, "end_sample": 1, "sample_rate": 16000}
    elif kind == "translation":
        arguments = ["--m2m100-worker", "--rust-protocol", str(root / "missing-mt"),
                     str(root / "missing-tokenizer"), "2"]
        payload = {"text": text, "context": {}}
    else:
        arguments = ["--correction-worker"]
    log = (root / f"worker-{kind}.stderr.log").open("xb")
    try:
        process = subprocess.Popen([str(binary), *arguments], cwd=root, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, bufsize=0,
                                   start_new_session=os.name == "posix")
    except BaseException:
        log.close()
        raise
    owned = Owned(process, api)
    try:
        owned.snapshot()
        request = request_frame(kind, payload)
        error = None if kind == "correction" else f"{kind}_worker_failed"
        validate_reply(exchange(owned, request), request, text if error is None else None, error)
        if kind == "correction":
            second = request_frame(kind, payload, 2)
            validate_reply(exchange(owned, second), second, text, None)
            require(process.poll() is None, "correction_exited_before_EOF")
            process.stdin.close()
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            owned.snapshot()
            time.sleep(.05)
        require(process.wait(timeout=1) == (0 if kind == "correction" else 1),
                "worker_terminal_exit_code")
        owned.gone(list(owned.seen.values()))
        return {"task": kind, "status": "passed", "identity_utf8_reply": True,
                "generic_error": error, "eof_checked": kind == "correction",
                "exit_code": process.returncode, "observed_processes": len(owned.seen),
                "ownership_observations": owned.observations, "owned_processes_absent": True}
    finally:
        try:
            owned.cleanup()
        finally:
            log.close()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.stdout.close()


def backend_probe(binary: Path, root: Path, env: dict, api) -> dict:
    fake = root / "fake-whisper"
    fake.mkdir()
    for name in ("config.json", "tokenizer.json", "model.bin"):
        (fake / name).write_bytes(b"{}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env.update({"LANGTEXTFLOW_BACKEND_PORT": str(port),
                "LANGTEXTFLOW_FASTER_WHISPER_MODEL": str(fake),
                "LANGTEXTFLOW_FASTER_WHISPER_DEVICE": "cpu",
                "LANGTEXTFLOW_FASTER_WHISPER_COMPUTE_TYPE": "int8",
                "LANGTEXTFLOW_FASTER_WHISPER_CHUNK_SECONDS": "4",
                "LANGTEXTFLOW_MAX_SEGMENTS": "100"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    log = (root / "backend.log").open("xb")
    graph_log = (root / "backend-process-graph.log").open("x", encoding="utf-8")
    try:
        process = subprocess.Popen([str(binary)], cwd=root, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=os.name == "posix")
    except BaseException:
        log.close()
        graph_log.close()
        raise
    owned = Owned(process, api)

    def request(path, payload=None):
        if payload is not None:
            owned.listener_owned(port)  # Never stop a foreign process that won the port race.
        body = json.dumps(payload).encode() if payload is not None else None
        operation = Request(f"http://127.0.0.1:{port}" + path, data=body,
                            headers={"Content-Type": "application/json"})
        with opener.open(operation, timeout=30) as response:
            data = response.read(65537)
        require(len(data) <= 65536, "oversized_loopback_response")
        return json.loads(data)

    cycles = []
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            require(process.poll() is None, "backend_startup_exit")
            owned.snapshot()
            try:
                if request("/health") == {"status": "ok"}:
                    break
            except URLError:
                pass
            time.sleep(.1)
        else:
            raise SmokeFailure("backend_startup_deadline")
        for _ in range(2):
            state = request("/api/v1/session/start", {
                "engine": "faster-whisper", "source_language": "ko", "target_languages": ["en"],
                "correction_provider": "none", "translation_provider": "none",
                "context": {"title": "Frozen idle wiring", "audience_access": False},
            })
            require(state.get("running") is True and bool(state.get("session_id")), "start_failed")
            runtime, worker = recorded_direct_asr(owned, graph_log)
            # Idle observation catches a detached inner bootloader/early exit;
            # actual Python/model readiness still needs the separate native proof.
            idle_until = time.monotonic() + 2
            while time.monotonic() < idle_until:
                current_runtime, current_worker = recorded_direct_asr(owned, graph_log)
                require(current_runtime["pid"] == runtime["pid"]
                        and current_worker["pid"] == worker["pid"], "idle_worker_replaced")
                time.sleep(.1)
            require(not cycles or cycles[0]["session_id"] != state["session_id"], "restart_same_id")
            stopped = request("/api/v1/session/stop", {"expected_session_id": state["session_id"]})
            require(stopped.get("running") is False, "stop_failed")
            owned.gone([runtime, worker])
            with sqlite3.connect(root / "smoke.sqlite", timeout=2) as database:
                row = database.execute("SELECT ended_at FROM sessions WHERE session_id=?",
                                       (state["session_id"],)).fetchone()
            require(row is not None and row[0] is not None, "session_not_ended_in_sqlite")
            cycles.append({"session_id": state["session_id"], "ended_at": row[0],
                           "rust_pid": runtime["pid"], "asr_pid": worker["pid"],
                           "asr_parent_pid": worker["ppid"], "direct_pid_no_inner": True,
                           "idle_observation_seconds": 2})
        request("/api/v1/desktop/shutdown", {})
        require(process.wait(timeout=20) == 0, "backend_shutdown_exit")
        owned.gone(list(owned.seen.values()))
        return {"status": "passed", "session_start_stop_restart": cycles,
                "runtime_bundled_auto_resolution": True, "no_audio_fed": True,
                "valid_model_assets_supplied": False, "model_load_or_recognition_proven": False,
                "owned_processes_absent": True, "observations": owned.observations}
    finally:
        try:
            owned.cleanup()
        finally:
            log.close()
            graph_log.close()


def main(binary_path: Path, output_receipt: Path) -> dict:
    output_receipt.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Reserve before any process or fixture is created. A second invocation
        # cannot overwrite either an earlier receipt or a concurrent attempt.
        output = output_receipt.open("x", encoding="utf-8")
    except FileExistsError:
        raise SmokeFailure("receipt_already_exists") from None
    report = {"status": "failed", "scope": "frozen protocol and start-idle wiring only",
              "field_ready": False, "valid_models_supplied": False, "model_quality_test": False,
              "probes": [], "stage": "preflight", "evidence_visibility": "private"}
    scratch = None
    try:
        scratch = Path(tempfile.mkdtemp(prefix="rust-frozen-smoke-", dir=output_receipt.parent))
        report["private_fixture"] = scratch.name
        require(binary_path.is_file(), "binary_missing")
        with binary_path.open("rb") as stream:
            report["binary_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        api = process_api()
        env = environment(scratch)
        for kind in ("correction", "asr", "translation"):
            report["stage"] = kind
            report["probes"].append(worker_probe(binary_path.resolve(), scratch, env, api, kind))
        report["stage"] = "backend_start_idle"
        report["backend"] = backend_probe(binary_path.resolve(), scratch, env, api)
        report["status"] = "passed"
    except Exception as exc:
        report["error_category"] = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
    finally:
        if scratch is not None:
            report["private_log_files"] = sorted(path.name for path in scratch.glob("*.log"))
            if report["status"] == "passed":
                try:
                    shutil.rmtree(scratch)
                except OSError:
                    report["status"] = "failed"
                    report["scratch_cleanup_error"] = True
            report["private_fixture_retained"] = scratch.exists()
        with output:
            output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("output_receipt", type=Path)
    arguments = parser.parse_args()
    receipt = main(arguments.binary, arguments.output_receipt)
    raise SystemExit(0 if receipt["status"] == "passed" else 1)
