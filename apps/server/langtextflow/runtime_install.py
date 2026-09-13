from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from .config import Settings

VIBEVOICE_MODEL_REVISION = "c0b4d1571323d98254b7a743e7fe7e543b792caa"


class RuntimeKind(StrEnum):
    FASTER_WHISPER = "faster-whisper"
    OLLAMA = "ollama"
    VIBEVOICE = "vibevoice"


class ProvisionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class RuntimeStatus(BaseModel):
    kind: RuntimeKind
    available: bool
    managed: bool = False
    path: str | None = None
    detail: str = ""


class RuntimeProvisionJob(BaseModel):
    job_id: str
    kind: RuntimeKind
    state: ProvisionState = ProvisionState.QUEUED
    status: str = "queued"
    error: str | None = None
    details: dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


Runner = Callable[[str], Awaitable[None]]


class RuntimeProvisionManager:
    """Provision supported runtimes using fixed, non-user-controlled commands."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jobs: dict[str, RuntimeProvisionJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    @property
    def runtime_root(self) -> Path:
        return Path(self.settings.managed_runtime_dir).expanduser().resolve()

    @property
    def cache_root(self) -> Path:
        return Path(self.settings.model_cache_dir).expanduser().resolve()

    async def statuses(self) -> list[RuntimeStatus]:
        return [
            await self.status(RuntimeKind.FASTER_WHISPER),
            await self.status(RuntimeKind.OLLAMA),
            await self.status(RuntimeKind.VIBEVOICE),
        ]

    async def status(self, kind: RuntimeKind) -> RuntimeStatus:
        if kind is RuntimeKind.FASTER_WHISPER:
            available = importlib.util.find_spec("faster_whisper") is not None
            return RuntimeStatus(
                kind=kind,
                available=available,
                managed=bool(getattr(sys, "frozen", False)),
                path=sys.executable if available else None,
                detail="bundled with desktop" if available and getattr(sys, "frozen", False) else "",
            )
        if kind is RuntimeKind.OLLAMA:
            executable = shutil.which("ollama")
            healthy = False
            if executable:
                try:
                    async with httpx.AsyncClient(timeout=1.5) as client:
                        response = await client.get(f"{self.settings.ollama_url.rstrip('/')}/api/tags")
                        healthy = response.is_success
                except Exception:
                    healthy = False
            return RuntimeStatus(
                kind=kind,
                available=bool(executable),
                managed=False,
                path=executable,
                detail="running" if healthy else ("installed, not running" if executable else "not installed"),
            )

        repo = self._vibevoice_repo()
        python = self._vibevoice_python()
        available = repo.is_dir() and python.is_file()
        return RuntimeStatus(
            kind=kind,
            available=available,
            managed=available,
            path=str(repo) if available else None,
            detail=self.settings.vibevoice_repo_ref if available else "not installed",
        )

    async def start(self, kind: RuntimeKind) -> RuntimeProvisionJob:
        runner: Runner
        if kind is RuntimeKind.FASTER_WHISPER:
            runner = self._install_faster_whisper
        elif kind is RuntimeKind.OLLAMA:
            runner = self._install_ollama
        else:
            runner = self._install_vibevoice

        async with self._lock:
            for job in self._jobs.values():
                if job.kind is kind and job.state in {ProvisionState.QUEUED, ProvisionState.RUNNING}:
                    return job.model_copy(deep=True)
            job = RuntimeProvisionJob(job_id=uuid4().hex, kind=kind)
            self._jobs[job.job_id] = job
            task = asyncio.create_task(runner(job.job_id), name=f"provision-{kind}-{job.job_id}")
            self._tasks[job.job_id] = task
            return job.model_copy(deep=True)

    async def get_job(self, job_id: str) -> RuntimeProvisionJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    async def list_jobs(self) -> list[RuntimeProvisionJob]:
        async with self._lock:
            return [item.model_copy(deep=True) for item in self._jobs.values()]

    async def cancel(self, job_id: str) -> RuntimeProvisionJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            task = self._tasks.get(job_id)
            if job is None:
                return None
            if job.state in {ProvisionState.COMPLETED, ProvisionState.CANCELLED, ProvisionState.ERROR}:
                return job.model_copy(deep=True)
            job.state = ProvisionState.CANCELLED
            job.status = "cancelled"
            job.updated_at = datetime.now(UTC)
            job.finished_at = job.updated_at
            if task:
                task.cancel()
        if task:
            with suppress(asyncio.CancelledError):
                await task
        return await self.get_job(job_id)

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = [task for task in self._tasks.values() if not task.done()]
            for task in tasks:
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _install_faster_whisper(self, job_id: str) -> None:
        if importlib.util.find_spec("faster_whisper") is not None:
            await self._complete(job_id, "faster-whisper is already available")
            return
        if getattr(sys, "frozen", False):
            await self._error(job_id, "desktop bundle does not contain faster-whisper")
            return
        await self._run_fixed(
            job_id,
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "faster-whisper>=1.2.1,<2"],
            "installing faster-whisper runtime",
        )

    async def _install_ollama(self, job_id: str) -> None:
        if shutil.which("ollama"):
            await self._complete(job_id, "Ollama is already installed")
            return
        if sys.platform == "win32" and shutil.which("winget"):
            command = [
                "winget", "install", "--id", "Ollama.Ollama", "--exact", "--silent",
                "--accept-package-agreements", "--accept-source-agreements",
            ]
        elif sys.platform == "darwin" and shutil.which("brew"):
            command = ["brew", "install", "ollama"]
        else:
            await self._error(job_id, "automatic Ollama install requires winget on Windows or Homebrew on macOS")
            return
        await self._run_fixed(job_id, command, "installing Ollama")

    async def _install_vibevoice(self, job_id: str) -> None:
        if not shutil.which("git"):
            await self._error(job_id, "git is required to provision VibeVoice")
            return
        if not shutil.which("ffmpeg"):
            await self._error(job_id, "ffmpeg is required before VibeVoice can be provisioned")
            return

        repo = self._vibevoice_repo()
        python = self._vibevoice_python()
        repo.parent.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        await self._running(job_id, "preparing pinned VibeVoice source")
        try:
            if not (repo / ".git").exists():
                await _run(["git", "clone", "--filter=blob:none", "--no-checkout", self.settings.vibevoice_repo_url, str(repo)])
            await _run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", self.settings.vibevoice_repo_ref])
            await _run(["git", "-C", str(repo), "checkout", "--force", self.settings.vibevoice_repo_ref])
            if not python.exists():
                await _run([sys.executable, "-m", "venv", str(repo / ".venv")])
            await self._running(job_id, "installing pinned VibeVoice runtime")
            await _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(repo)])
            model_dir = self.cache_root / "vibevoice" / VIBEVOICE_MODEL_REVISION
            model_dir.parent.mkdir(parents=True, exist_ok=True)
            code = (
                "from huggingface_hub import snapshot_download; import sys; "
                "print(snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3]))"
            )
            await self._running(job_id, "downloading pinned VibeVoice model snapshot")
            await _run([str(python), "-c", code, self.settings.vibevoice_model_id, VIBEVOICE_MODEL_REVISION, str(model_dir)])
            await self._complete(
                job_id,
                "VibeVoice runtime and model are ready",
                {"repo": str(repo), "python": str(python), "model": str(model_dir)},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._error(job_id, str(exc))

    def _vibevoice_repo(self) -> Path:
        if self.settings.vibevoice_repo_path:
            return Path(self.settings.vibevoice_repo_path).expanduser().resolve()
        return self.runtime_root / "vibevoice"

    def _vibevoice_python(self) -> Path:
        if self.settings.vibevoice_python:
            return Path(self.settings.vibevoice_python).expanduser().resolve()
        repo = self._vibevoice_repo()
        return repo / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    async def _run_fixed(self, job_id: str, command: list[str], status: str) -> None:
        await self._running(job_id, status)
        try:
            await _run(command)
            await self._complete(job_id, "runtime provisioning completed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._error(job_id, str(exc))

    async def _running(self, job_id: str, status: str) -> None:
        await self._update(job_id, state=ProvisionState.RUNNING, status=status)

    async def _complete(self, job_id: str, status: str, details: dict[str, str] | None = None) -> None:
        await self._update(job_id, state=ProvisionState.COMPLETED, status=status, details=details, finished=True)

    async def _error(self, job_id: str, error: str) -> None:
        await self._update(job_id, state=ProvisionState.ERROR, status="runtime provisioning failed", error=error[:2000], finished=True)

    async def _update(
        self,
        job_id: str,
        *,
        state: ProvisionState,
        status: str,
        error: str | None = None,
        details: dict[str, str] | None = None,
        finished: bool = False,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.state = state
            job.status = status
            job.error = error
            if details:
                job.details.update(details)
            job.updated_at = datetime.now(UTC)
            if finished:
                job.finished_at = job.updated_at
                self._tasks.pop(job_id, None)


async def _run(command: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        with suppress(ProcessLookupError):
            process.terminate()
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=3.0)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        raise
    if process.returncode != 0:
        output = (stderr or stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(output[-4000:] or f"command exited with {process.returncode}")
