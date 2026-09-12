from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import shutil
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from .config import Settings

HealthProbe = Callable[[], Awaitable[bool]]
ProcessSpawner = Callable[[list[str], str, dict[str, str]], Awaitable[Any]]


class VibeVoiceLifecycleMode(StrEnum):
    UNCONFIGURED = "unconfigured"
    STOPPED = "stopped"
    STARTING = "starting"
    MANAGED = "managed"
    EXTERNAL = "external"
    ERROR = "error"


class VibeVoiceLifecycleState(BaseModel):
    mode: VibeVoiceLifecycleMode
    configured: bool
    managed: bool
    running: bool
    healthy: bool
    pid: int | None = None
    status: str
    error: str | None = None
    url: str
    repo_path: str | None = None
    model_path: str | None = None
    started_at: datetime | None = None
    log_tail: list[str] = Field(default_factory=list)


class VibeVoiceLifecycleManager:
    """Start and stop only a pre-provisioned VibeVoice streaming runtime.

    This manager deliberately does not clone repositories, install Python/system
    packages, download model weights, or mutate a checkpoint tokenizer. Those
    provisioning steps belong to an explicit installer/runtime setup flow.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        probe: HealthProbe | None = None,
        spawn: ProcessSpawner | None = None,
    ) -> None:
        self.settings = settings
        self._probe_override = probe
        self._spawn_override = spawn
        self._process: Any | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._log_task: asyncio.Task[None] | None = None
        self._logs: deque[str] = deque(maxlen=80)
        self._last_error: str | None = None
        self._started_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    async def status(self) -> VibeVoiceLifecycleState:
        healthy = await self._probe_health()
        configuration_error = self._configuration_error()
        async with self._lock:
            process = self._process
            process_live = process is not None and process.returncode is None
            start_pending = self._start_task is not None and not self._start_task.done()
            configured = configuration_error is None

            if healthy:
                managed = process_live
                mode = (
                    VibeVoiceLifecycleMode.MANAGED
                    if managed
                    else VibeVoiceLifecycleMode.EXTERNAL
                )
                return self._state(
                    mode,
                    configured=configured,
                    managed=managed,
                    running=True,
                    healthy=True,
                    status=(
                        "LangTextFlow가 시작한 VibeVoice sidecar가 준비되었습니다."
                        if managed
                        else "외부에서 실행 중인 VibeVoice sidecar가 응답합니다."
                    ),
                )

            if process_live:
                mode = (
                    VibeVoiceLifecycleMode.STARTING
                    if start_pending
                    else VibeVoiceLifecycleMode.ERROR
                )
                return self._state(
                    mode,
                    configured=configured,
                    managed=True,
                    running=True,
                    healthy=False,
                    status=(
                        "VibeVoice sidecar가 시작 중입니다."
                        if start_pending
                        else (
                            "VibeVoice 프로세스는 실행 중이지만 "
                            "health check에 응답하지 않습니다."
                        )
                    ),
                    error=None if start_pending else self._last_error,
                )

            if configuration_error is not None:
                return self._state(
                    VibeVoiceLifecycleMode.UNCONFIGURED,
                    configured=False,
                    managed=False,
                    running=False,
                    healthy=False,
                    status="관리형 VibeVoice 실행 경로가 아직 준비되지 않았습니다.",
                    error=configuration_error,
                )

            if self._last_error is not None:
                return self._state(
                    VibeVoiceLifecycleMode.ERROR,
                    configured=True,
                    managed=False,
                    running=False,
                    healthy=False,
                    status="VibeVoice sidecar 시작 또는 실행에 실패했습니다.",
                    error=self._last_error,
                )

            return self._state(
                VibeVoiceLifecycleMode.STOPPED,
                configured=True,
                managed=False,
                running=False,
                healthy=False,
                status="VibeVoice sidecar를 시작할 수 있습니다.",
            )

    async def start(self) -> VibeVoiceLifecycleState:
        async with self._operation_lock:
            current = await self.status()
            if current.healthy:
                return current
            configuration_error = self._configuration_error()
            if configuration_error is not None:
                raise ValueError(configuration_error)

            async with self._lock:
                if self._start_task is not None and not self._start_task.done():
                    return self._state(
                        VibeVoiceLifecycleMode.STARTING,
                        configured=True,
                        managed=self._process is not None,
                        running=self._process is not None,
                        healthy=False,
                        status="VibeVoice sidecar가 시작 중입니다.",
                    )
                self._last_error = None
                self._started_at = datetime.now(UTC)
                self._logs.clear()
                self._start_task = asyncio.create_task(
                    self._run_start(),
                    name="vibevoice-sidecar-start",
                )

            return await self.status()

    async def stop(self) -> VibeVoiceLifecycleState:
        async with self._operation_lock:
            async with self._lock:
                start_task = self._start_task
                if start_task is not None and not start_task.done():
                    start_task.cancel()

            if start_task is not None and not start_task.done():
                with suppress(asyncio.CancelledError):
                    await start_task

            async with self._lock:
                managed = self._process is not None
            if managed:
                await self._terminate_owned_process()
                async with self._lock:
                    self._last_error = None
                    self._started_at = None

            current = await self.status()
            if current.mode is VibeVoiceLifecycleMode.EXTERNAL:
                return current.model_copy(
                    update={
                        "status": (
                            "외부 VibeVoice sidecar는 LangTextFlow가 소유하지 않아 "
                            "종료하지 않았습니다."
                        )
                    }
                )
            return current

    async def shutdown(self) -> None:
        async with self._lock:
            owned = self._process is not None
        if owned:
            await self.stop()

    async def _run_start(self) -> None:
        process: Any | None = None
        try:
            command, cwd, env = self._build_command()
            process = await self._spawn_process(command, cwd, env)
            async with self._lock:
                self._process = process
                if getattr(process, "stdout", None) is not None:
                    self._log_task = asyncio.create_task(
                        self._drain_logs(process.stdout),
                        name="vibevoice-sidecar-logs",
                    )

            deadline = (
                asyncio.get_running_loop().time()
                + max(1.0, self.settings.vibevoice_startup_timeout_seconds)
            )
            while asyncio.get_running_loop().time() < deadline:
                if process.returncode is not None:
                    raise RuntimeError(
                        "VibeVoice process exited before becoming ready: "
                        f"{process.returncode}"
                    )
                if await self._probe_health():
                    async with self._lock:
                        self._last_error = None
                    return
                await asyncio.sleep(0.5)
            raise TimeoutError("VibeVoice sidecar startup health check timed out")
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                await self._terminate_owned_process()
            raise
        except Exception as exc:
            async with self._lock:
                self._last_error = str(exc)
            if process is not None and process.returncode is None:
                await self._terminate_owned_process()
        finally:
            async with self._lock:
                if self._start_task is asyncio.current_task():
                    self._start_task = None

    async def _probe_health(self) -> bool:
        if self._probe_override is not None:
            try:
                return bool(await self._probe_override())
            except Exception:
                return False
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                response = await client.get(
                    f"{self.settings.vibevoice_url.rstrip('/')}/v1/config"
                )
                response.raise_for_status()
            return True
        except Exception:
            return False

    def _configuration_error(self) -> str | None:
        parsed = urlparse(self.settings.vibevoice_url)
        if parsed.scheme != "http" or not parsed.hostname:
            return "관리형 VibeVoice URL은 loopback HTTP 주소여야 합니다."
        if not _is_loopback_host(parsed.hostname):
            return "관리형 VibeVoice는 localhost/loopback 주소에서만 시작할 수 있습니다."
        try:
            _managed_port(self.settings.vibevoice_url)
        except ValueError as exc:
            return str(exc)

        repo = (
            Path(self.settings.vibevoice_repo_path).expanduser()
            if self.settings.vibevoice_repo_path
            else None
        )
        if repo is None or not repo.is_dir():
            return "VibeVoice repository 경로를 설정하고 먼저 runtime을 준비하세요."
        server_module = repo / "vllm_plugin" / "asr_streaming_server.py"
        if not server_module.is_file():
            return (
                "VibeVoice repository에서 "
                "vllm_plugin/asr_streaming_server.py를 찾지 못했습니다."
            )

        model = (
            Path(self.settings.vibevoice_model_path).expanduser()
            if self.settings.vibevoice_model_path
            else None
        )
        if model is None or not model.is_dir():
            return "준비된 VibeVoice streaming checkpoint의 로컬 경로를 설정하세요."
        if not (model / "preprocessor_config.json").is_file():
            return "VibeVoice checkpoint에 preprocessor_config.json이 없습니다."
        tokenizer = model / "added_tokens.json"
        if not tokenizer.is_file():
            return "streaming tokenizer가 준비되지 않았습니다. 공식 준비 절차를 먼저 실행하세요."
        try:
            tokens = json.loads(tokenizer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "VibeVoice added_tokens.json을 읽을 수 없습니다."
        if not isinstance(tokens, dict) or "<|text_chunk_end|>" not in tokens:
            return "checkpoint tokenizer에 <|text_chunk_end|>가 없어 streaming 준비가 필요합니다."

        python = self.settings.vibevoice_python.strip() or sys.executable
        if Path(python).is_absolute():
            if not Path(python).is_file():
                return "설정한 VibeVoice Python 실행 파일을 찾지 못했습니다."
        elif shutil.which(python) is None:
            return "설정한 VibeVoice Python 실행 파일을 PATH에서 찾지 못했습니다."
        return None

    def _build_command(self) -> tuple[list[str], str, dict[str, str]]:
        error = self._configuration_error()
        if error is not None:
            raise ValueError(error)
        repo = str(Path(self.settings.vibevoice_repo_path).expanduser().resolve())
        model = str(Path(self.settings.vibevoice_model_path).expanduser().resolve())
        python = self.settings.vibevoice_python.strip() or sys.executable
        port = _managed_port(self.settings.vibevoice_url)
        command = [
            python,
            "-m",
            "vllm_plugin.asr_streaming_server",
            "--model",
            model,
            "--served-model-name",
            "vibevoice",
            "--port",
            str(port),
            "--dtype",
            "bfloat16",
            "--tensor-parallel-size",
            str(max(1, self.settings.vibevoice_tensor_parallel_size)),
            "--max-model-len",
            str(max(1, self.settings.vibevoice_max_model_len)),
            "--max-audio-windows",
            str(max(1, self.settings.vibevoice_max_audio_windows)),
            "--mm-processor-cache-gb",
            str(max(0.0, self.settings.vibevoice_mm_processor_cache_gb)),
            "--gpu-memory-utilization",
            str(min(1.0, max(0.01, self.settings.vibevoice_gpu_memory_utilization))),
        ]
        env = os.environ.copy()
        return command, repo, env

    async def _spawn_process(
        self,
        command: list[str],
        cwd: str,
        env: dict[str, str],
    ) -> Any:
        if self._spawn_override is not None:
            return await self._spawn_override(command, cwd, env)
        return await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def _drain_logs(self, stream: Any) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self._logs.append(text[-500:])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logs.append(f"[log reader error] {exc}")

    async def _terminate_owned_process(self) -> None:
        async with self._lock:
            process = self._process
            log_task = self._log_task
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=8.0)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if log_task is not None and not log_task.done():
            log_task.cancel()
            with suppress(asyncio.CancelledError):
                await log_task
        async with self._lock:
            self._process = None
            self._log_task = None

    def _state(
        self,
        mode: VibeVoiceLifecycleMode,
        *,
        configured: bool,
        managed: bool,
        running: bool,
        healthy: bool,
        status: str,
        error: str | None = None,
    ) -> VibeVoiceLifecycleState:
        process = self._process
        pid = getattr(process, "pid", None) if managed else None
        started_at = (
            self._started_at
            if managed or mode is VibeVoiceLifecycleMode.STARTING
            else None
        )
        return VibeVoiceLifecycleState(
            mode=mode,
            configured=configured,
            managed=managed,
            running=running,
            healthy=healthy,
            pid=pid,
            status=status,
            error=error,
            url=self.settings.vibevoice_url,
            repo_path=self.settings.vibevoice_repo_path or None,
            model_path=self.settings.vibevoice_model_path or None,
            started_at=started_at,
            log_tail=list(self._logs),
        )


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _managed_port(url: str) -> int:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("VibeVoice URL port가 올바르지 않습니다.") from exc
    if port is None:
        port = 80
    if port < 1 or port > 65535:
        raise ValueError("VibeVoice URL port가 올바르지 않습니다.")
    return port
