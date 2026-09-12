from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from .config import Settings

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_TERMINAL_STATES = {"completed", "cancelled", "error"}


class SetupJobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class ModelSetupRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)

    def normalized_model(self) -> str:
        value = self.model.strip()
        if not _MODEL_NAME_RE.fullmatch(value):
            raise ValueError("model name contains unsupported characters")
        return value


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
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class ModelSetupManager:
    """Owns explicit local model preparation jobs.

    The manager intentionally does not install operating-system packages or drivers.
    It only talks to already configured local providers and performs model preparation
    that those providers expose through their normal local APIs.
    """

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
        self._active_keys: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    def _client(self, *, timeout: httpx.Timeout | float | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport)

    async def list_jobs(self) -> list[ModelSetupJob]:
        async with self._lock:
            jobs = [job.model_copy(deep=True) for job in self._jobs.values()]
        return sorted(jobs, key=lambda item: item.started_at, reverse=True)

    async def get_job(self, job_id: str) -> ModelSetupJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job is not None else None

    async def start_ollama_pull(self, model: str) -> ModelSetupJob:
        normalized = _normalize_model_name(model)
        key = ("ollama", normalized.casefold())
        async with self._lock:
            existing_id = self._active_keys.get(key)
            if existing_id is not None:
                return self._jobs[existing_id].model_copy(deep=True)

            job = ModelSetupJob(
                job_id=uuid4().hex,
                provider="ollama",
                model=normalized,
            )
            self._jobs[job.job_id] = job
            self._active_keys[key] = job.job_id
            self._trim_jobs_locked()
            task = asyncio.create_task(
                self._run_ollama_pull(job.job_id),
                name=f"ollama-pull-{job.job_id}",
            )
            self._tasks[job.job_id] = task
            return job.model_copy(deep=True)

    async def cancel_job(self, job_id: str) -> ModelSetupJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            task = self._tasks.get(job_id)
            if job is None:
                return None
            if job.state.value in _TERMINAL_STATES:
                return job.model_copy(deep=True)
            if task is not None:
                task.cancel()

        if task is not None:
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

    async def _run_ollama_pull(self, job_id: str) -> None:
        await self._update_job(
            job_id,
            state=SetupJobState.RUNNING,
            status="Ollama 모델 다운로드를 시작합니다.",
        )
        try:
            timeout = httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
            async with self._client(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.settings.ollama_url.rstrip('/')}/api/pull",
                    json={"model": self._jobs[job_id].model, "stream": True},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        payload = _parse_progress_line(line)
                        if payload is None:
                            continue
                        await self._apply_ollama_progress(job_id, payload)

            await self._update_job(
                job_id,
                state=SetupJobState.COMPLETED,
                status="모델 준비가 완료되었습니다.",
                progress_percent=100.0,
                finished=True,
            )
        except asyncio.CancelledError:
            await self._update_job(
                job_id,
                state=SetupJobState.CANCELLED,
                status="다운로드 요청을 취소했습니다.",
                finished=True,
            )
            raise
        except Exception as exc:
            await self._update_job(
                job_id,
                state=SetupJobState.ERROR,
                status="모델 다운로드에 실패했습니다.",
                error=str(exc),
                finished=True,
            )
        finally:
            await self._release_job(job_id)

    async def _apply_ollama_progress(self, job_id: str, payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "downloading")
        digest = _optional_string(payload.get("digest"))
        completed = _optional_int(payload.get("completed"))
        total = _optional_int(payload.get("total"))
        percent: float | None = None
        if completed is not None and total is not None and total > 0:
            percent = round(min(100.0, max(0.0, completed / total * 100.0)), 1)
        await self._update_job(
            job_id,
            status=status,
            digest=digest,
            completed_bytes=completed,
            total_bytes=total,
            progress_percent=percent,
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
            (
                job
                for job in self._jobs.values()
                if job.state.value in _TERMINAL_STATES
            ),
            key=lambda item: item.updated_at,
        )
        for job in finished[: max(0, len(self._jobs) - maximum)]:
            self._jobs.pop(job.job_id, None)


def _normalize_model_name(model: str) -> str:
    value = model.strip()
    if not _MODEL_NAME_RE.fullmatch(value):
        raise ValueError("model name contains unsupported characters")
    return value


def _parse_progress_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


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
