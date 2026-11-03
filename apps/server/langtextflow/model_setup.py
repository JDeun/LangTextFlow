from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from .config import Settings
from .m2m100_cache import (
    m2m100_cache_paths,
    managed_m2m100_root,
    publish_m2m100_cache,
    uses_managed_m2m100,
)
from .m2m100_prepare_worker import (
    DOWNLOAD_BYTES,
    MODEL_ID,
    PREPARE_TIMEOUT_SECONDS,
    preparation_progress,
)
from .provider_http import bounded_response_lines, provider_stream
from .storage_guard import ensure_storage_capacity
from .whisper_cache import managed_whisper_cache, resolve_whisper_model

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_TERMINAL_STATES = {"completed", "cancelled", "error"}
_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_WHISPER_DOWNLOAD_ESTIMATES = {
    "tiny": 200 * _MIB,
    "base": 350 * _MIB,
    "small": 1 * _GIB,
    "medium": 3 * _GIB,
    "large": 6 * _GIB,
    "large-v1": 6 * _GIB,
    "large-v2": 6 * _GIB,
    "large-v3": 6 * _GIB,
    "large-v3-turbo": 4 * _GIB,
    "turbo": 4 * _GIB,
}


class SetupJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ModelSetupRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)

    def normalized_model(self) -> str:
        return _normalize_model_name(self.model)


class ModelSetupJob(BaseModel):
    job_id: str
    provider: str
    model: str
    state: SetupJobState = SetupJobState.QUEUED
    status: str = "queued"
    digest: str | None = None
    completed_bytes: int | None = None
    total_bytes: int | None = None
    progress_percent: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class ModelSetupManager:
    """Own explicit model preparation without installing OS packages or drivers."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._jobs: dict[str, ModelSetupJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._active_keys: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        self._native_cleanup: set[asyncio.Task] = set()
        self._native_started: set[str] = set()
        self._native_committed: set[str] = set()

    @property
    def m2m100_preparing(self) -> bool:
        return ("m2m100-ct2", MODEL_ID.casefold()) in self._active_keys

    async def start_m2m100_prepare(self) -> ModelSetupJob:
        if not uses_managed_m2m100(self.settings):
            raise RuntimeError("Custom M2M100 paths use manual offline provisioning")
        if any(importlib.util.find_spec(name) is None
               for name in ("ctranslate2", "transformers", "sentencepiece")):
            raise RuntimeError("M2M100 runtime is missing; install the local-mt application extra")
        return await self._start_job(provider="m2m100-ct2", model=MODEL_ID,
                                     runner=self._run_m2m100_prepare, task_prefix="m2m100-prepare")

    @property
    def _reserve_bytes(self) -> int:
        return self.settings.storage_reserve_mb * _MIB

    def _client(self, *, timeout: httpx.Timeout | float | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def list_jobs(self) -> list[ModelSetupJob]:
        async with self._lock:
            jobs = [job.model_copy(deep=True) for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: item.started_at, reverse=True)

    async def get_job(self, job_id: str) -> ModelSetupJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            snapshot = job.model_copy(deep=True) if job is not None else None
        if snapshot is not None and snapshot.provider == "m2m100-ct2":
            progress = await asyncio.to_thread(
                preparation_progress, managed_m2m100_root(self.settings) / snapshot.job_id,
            )
            snapshot.details.update(progress)
        return snapshot

    async def start_ollama_pull(self, model: str) -> ModelSetupJob:
        return await self._start_job(
            provider="ollama",
            model=model,
            runner=self._run_ollama_pull,
            task_prefix="ollama-pull",
        )

    async def start_faster_whisper_prefetch(self, model: str) -> ModelSetupJob:
        if importlib.util.find_spec("faster_whisper") is None:
            raise RuntimeError(
                "faster-whisper is not installed; install LangTextFlow with the 'whisper' extra"
            )
        return await self._start_job(
            provider="faster-whisper",
            model=model,
            runner=self._run_faster_whisper_prefetch,
            task_prefix="faster-whisper-prefetch",
        )

    async def _start_job(
        self,
        *,
        provider: str,
        model: str,
        runner: Any,
        task_prefix: str,
    ) -> ModelSetupJob:
        normalized = _normalize_model_name(model)
        key = (provider, normalized.casefold())
        async with self._lock:
            existing_id = self._active_keys.get(key)
            if existing_id is not None:
                return self._jobs[existing_id].model_copy(deep=True)

            self._ensure_initial_storage(provider, normalized)
            job = ModelSetupJob(
                job_id=uuid4().hex,
                provider=provider,
                model=normalized,
            )
            self._jobs[job.job_id] = job
            self._active_keys[key] = job.job_id
            self._trim_jobs_locked()
            task = asyncio.create_task(
                runner(job.job_id),
                name=f"{task_prefix}-{job.job_id}",
            )
            self._tasks[job.job_id] = task
            return job.model_copy(deep=True)

    def _ensure_initial_storage(self, provider: str, model: str) -> None:
        if provider == "m2m100-ct2":
            ensure_storage_capacity(
                managed_m2m100_root(self.settings), required_bytes=DOWNLOAD_BYTES,
                reserve_bytes=self._reserve_bytes, operation="prepare local translation",
            )
            return
        if provider == "faster-whisper":
            required = _whisper_download_estimate(model)
            cache_dir = managed_whisper_cache(self.settings.model_cache_dir)
            try:
                resolve_whisper_model(model, cache_dir)
                return  # Reusing an existing snapshot does not need download-sized free space.
            except (ImportError, OSError, ValueError):
                pass
            ensure_storage_capacity(
                cache_dir,
                required_bytes=required,
                reserve_bytes=self._reserve_bytes,
                operation=f"prepare faster-whisper model {model}",
            )
            return
        if provider == "ollama":
            storage = self._local_ollama_storage_path()
            if storage is not None:
                ensure_storage_capacity(
                    storage,
                    reserve_bytes=self._reserve_bytes,
                    operation=f"start Ollama model download {model}",
                )

    def _local_ollama_storage_path(self) -> Path | None:
        parsed = urlparse(self.settings.ollama_url)
        if (parsed.hostname or "").casefold() not in {"localhost", "127.0.0.1", "::1"}:
            return None
        configured = os.environ.get("OLLAMA_MODELS", "").strip()
        return Path(configured).expanduser() if configured else Path.home() / ".ollama" / "models"

    async def cancel_job(self, job_id: str) -> ModelSetupJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            task = self._tasks.get(job_id)
            if job is None:
                return None
            if job.state.value in _TERMINAL_STATES:
                return job.model_copy(deep=True)
            if job_id in self._native_committed:
                return job.model_copy(deep=True)  # Atomic publication has already committed.

            now = datetime.now(UTC)
            job.state = SetupJobState.CANCELLED
            job.status = "다운로드 요청을 취소했습니다."
            job.updated_at = now
            job.finished_at = now
            if task is not None:
                task.cancel()

        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
        if job.provider != "m2m100-ct2" or job_id not in self._native_started:
            await self._release_job(job_id)
        return await self.get_job(job_id)

    async def _run_m2m100_prepare(self, job_id: str) -> None:
        from .translation.m2m100_ct2 import _M2M100Worker

        self._native_started.add(job_id)
        root = managed_m2m100_root(self.settings)
        stage = root / job_id
        worker = None
        published = False
        try:
            if root.is_symlink():
                raise ValueError("invalid managed cache")
            root.mkdir(parents=True, exist_ok=True)
            stage.mkdir()
            prefix = ([sys.executable, "--m2m100-prepare-worker"]
                      if getattr(sys, "frozen", False)
                      else [sys.executable, "-m", "langtextflow.m2m100_prepare_worker"])
            # The same single-child lease excludes native inference during cache preparation.
            try:
                old_model, old_tokenizer = m2m100_cache_paths(self.settings)
            except (OSError, ValueError):
                old_model, old_tokenizer = stage / "absent-model", stage / "absent-tokenizer"
            worker = _M2M100Worker([*prefix, str(stage), str(self._reserve_bytes),
                                  str(old_model), str(old_tokenizer)])

            def record_owner(launch: asyncio.Task) -> None:
                with suppress(Exception, asyncio.CancelledError):
                    (stage / "owner.json").write_text(json.dumps({"pid": launch.result().pid}))

            worker._launch.add_done_callback(record_owner)
            await self._update_job(job_id, state=SetupJobState.RUNNING,
                                   status="Verifying/preparing pinned M2M100 files (~0.5 GB).")
            await worker.start(timeout=PREPARE_TIMEOUT_SECONDS)
            async with self._lock:
                job = self._jobs[job_id]
                if job.state == SetupJobState.CANCELLED:
                    raise asyncio.CancelledError
                publish_m2m100_cache(root, job_id)
                published = True
                self._native_committed.add(job_id)
                job.status = "Verified cache published; waiting for native worker cleanup."
                job.updated_at = datetime.now(UTC)
        except asyncio.CancelledError:
            if not published:
                await self._mark_cancelled(job_id)
            raise
        except Exception as exc:
            # Worker/HTTP/native exceptions can contain local paths, signed URLs or credentials.
            await self._update_job(job_id, details={
                **preparation_progress(stage),
                "error_category": "deadline" if isinstance(exc, TimeoutError) else "prepare_failed",
            })
            await self._mark_error(job_id, "M2M100 preparation failed; previous cache retained.",
                                   RuntimeError("Check runtime dependencies, network and disk; "
                                                "retry preparation to repair the cache."))
        finally:
            if worker is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await worker.close()

            async def finish() -> None:
                if worker is not None:
                    await worker.released.wait()
                if not published:
                    await asyncio.to_thread(shutil.rmtree, stage, True)
                else:
                    await self._update_job(job_id, state=SetupJobState.COMPLETED,
                                           status="Pinned M2M100 CPU cache verified and ready.",
                                           progress_percent=100, finished=True)
                self._native_started.discard(job_id)
                self._native_committed.discard(job_id)
                await self._release_job(job_id)

            if worker is not None and not worker.released.is_set():
                # Never discard a staging directory or admit another job before a late child dies.
                cleanup = asyncio.create_task(finish(), name=f"m2m100-cleanup-{job_id}")
                self._native_cleanup.add(cleanup)
                cleanup.add_done_callback(self._native_cleanup.discard)
            else:
                await finish()

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = [task for task in self._tasks.values() if not task.done()]
            for task in tasks:
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        for job_id, job in list(self._jobs.items()):
            if job.provider == "m2m100-ct2" and job_id not in self._native_started:
                await self._release_job(job_id)

    async def _run_ollama_pull(self, job_id: str) -> None:
        job = await self.get_job(job_id)
        if job is None:
            return
        await self._update_job(
            job_id,
            state=SetupJobState.RUNNING,
            status="Ollama 모델 다운로드를 시작합니다.",
        )
        try:
            timeout = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
            succeeded = False
            async with (
                self._client(timeout=timeout) as client,
                provider_stream(
                    client,
                    "POST",
                    f"{self.settings.ollama_url.rstrip('/')}/api/pull",
                    json={"model": job.model, "stream": True},
                ) as response,
            ):
                async for line in bounded_response_lines(response):
                    if not line.strip():
                        continue
                    payload = _parse_progress_line(line)
                    if payload is None:
                        continue
                    if payload.get("error"):
                        raise ValueError("Ollama download provider reported an error")
                    succeeded = payload.get("status") == "success"
                    await self._apply_ollama_progress(job_id, payload)

            if not succeeded:
                raise ValueError("Ollama download stream ended before success")
            await self._update_job(
                job_id,
                state=SetupJobState.COMPLETED,
                status="모델 준비가 완료되었습니다.",
                progress_percent=100.0,
                finished=True,
            )
        except asyncio.CancelledError:
            await self._mark_cancelled(job_id)
            raise
        except Exception as exc:
            await self._mark_error(job_id, "모델 다운로드에 실패했습니다.", exc)
        finally:
            await self._release_job(job_id)

    async def _run_faster_whisper_prefetch(self, job_id: str) -> None:
        job = await self.get_job(job_id)
        if job is None:
            return
        await self._update_job(
            job_id,
            state=SetupJobState.RUNNING,
            status="faster-whisper 모델 파일을 준비하는 중입니다.",
        )
        cache_dir = managed_whisper_cache(self.settings.model_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        command = (
            [sys.executable, "--faster-whisper-prefetch-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "langtextflow.whisper_prefetch_worker"]
        )
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                job.model,
                str(cache_dir),
                stdout=asyncio.subprocess.PIPE,
                env={**os.environ, "HF_HOME": str(cache_dir)},
                stderr=asyncio.subprocess.PIPE,
            )
            async with self._lock:
                self._processes[job_id] = process
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                message = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(message or f"download process exited with {process.returncode}")
            details = _parse_last_json_line(stdout.decode("utf-8", errors="replace"))
            if not details or not isinstance(details.get("path"), str):
                raise ValueError("invalid Whisper preparation response")
            prepared = resolve_whisper_model(job.model, cache_dir)
            await self._update_job(
                job_id,
                state=SetupJobState.COMPLETED,
                status="faster-whisper 모델 준비가 완료되었습니다.",
                progress_percent=100.0,
                details={"path": str(prepared)},
                finished=True,
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                await _terminate_process(process)
            await self._mark_cancelled(job_id)
            raise
        except Exception as exc:
            await self._mark_error(job_id, "faster-whisper 모델 준비에 실패했습니다.", exc)
        finally:
            async with self._lock:
                self._processes.pop(job_id, None)
            await self._release_job(job_id)

    async def _apply_ollama_progress(self, job_id: str, payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "downloading")
        digest = _optional_string(payload.get("digest"))
        completed = _optional_int(payload.get("completed"))
        total = _optional_int(payload.get("total"))
        percent: float | None = None
        if completed is not None and total is not None and total > 0:
            percent = round(min(100.0, max(0.0, completed / total * 100.0)), 1)
            storage = self._local_ollama_storage_path()
            if storage is not None:
                ensure_storage_capacity(
                    storage,
                    required_bytes=max(0, total - completed),
                    reserve_bytes=self._reserve_bytes,
                    operation="finish the Ollama model download",
                )
        await self._update_job(
            job_id,
            status=status,
            digest=digest,
            completed_bytes=completed,
            total_bytes=total,
            progress_percent=percent,
        )

    async def _mark_cancelled(self, job_id: str) -> None:
        await self._update_job(
            job_id,
            state=SetupJobState.CANCELLED,
            status="다운로드 요청을 취소했습니다.",
            finished=True,
        )

    async def _mark_error(self, job_id: str, status: str, exc: Exception) -> None:
        await self._update_job(
            job_id,
            state=SetupJobState.ERROR,
            status=status,
            error=str(exc),
            finished=True,
        )

    async def _update_job(
        self,
        job_id: str,
        *,
        state: SetupJobState | None = None,
        status: str | None = None,
        digest: str | None = None,
        completed_bytes: int | None = None,
        total_bytes: int | None = None,
        progress_percent: float | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if state is not None:
                job.state = state
            if status is not None:
                job.status = status
            if digest is not None:
                job.digest = digest
            if completed_bytes is not None:
                job.completed_bytes = completed_bytes
            if total_bytes is not None:
                job.total_bytes = total_bytes
            if progress_percent is not None:
                job.progress_percent = progress_percent
            if details is not None:
                job.details.update(details)
            if error is not None:
                job.error = error
            now = datetime.now(UTC)
            job.updated_at = now
            if finished:
                job.finished_at = now

    async def _release_job(self, job_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                key = (job.provider, job.model.casefold())
                if self._active_keys.get(key) == job_id:
                    self._active_keys.pop(key, None)
            self._tasks.pop(job_id, None)

    def _trim_jobs_locked(self, *, maximum: int = 50) -> None:
        if len(self._jobs) <= maximum:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.state.value in _TERMINAL_STATES
             and job.job_id not in self._tasks),
            key=lambda item: item.updated_at,
        )
        for job in finished[: max(0, len(self._jobs) - maximum)]:
            self._jobs.pop(job.job_id, None)


def _normalize_model_name(model: str) -> str:
    value = model.strip()
    if not _MODEL_NAME_RE.fullmatch(value):
        raise ValueError("model name contains unsupported characters")
    return value


def _whisper_download_estimate(model: str) -> int:
    normalized = model.casefold().split("/")[-1]
    return _WHISPER_DOWNLOAD_ESTIMATES.get(normalized, 6 * _GIB)


def _parse_progress_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _parse_last_json_line(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        parsed = _parse_progress_line(line)
        if parsed is not None:
            return parsed
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
