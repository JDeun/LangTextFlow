"""Runtime installer task lifecycle fault injection (#82).

No dependency downloads, subprocesses, external services, or real file installs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from langtextflow.config import Settings
from langtextflow.runtime_install import (
    ProvisionState,
    RuntimeKind,
    RuntimeProvisionManager,
)


def _manager(tmp_path: Path) -> RuntimeProvisionManager:
    return RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"),
            model_cache_dir=str(tmp_path / "cache"),
        )
    )


@pytest.mark.asyncio
async def test_cancelling_active_provision_releases_task_and_allows_new_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    started = asyncio.Event()
    interrupted = asyncio.Event()

    async def fake_install(job_id: str) -> None:
        await manager._running(job_id, "installing fake runtime")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    monkeypatch.setattr(manager, "_install_faster_whisper", fake_install)
    initial = await manager.start(RuntimeKind.FASTER_WHISPER)
    await asyncio.wait_for(started.wait(), timeout=2)
    duplicate = await manager.start(RuntimeKind.FASTER_WHISPER)
    assert duplicate.job_id == initial.job_id
    assert (await manager.get_job(initial.job_id)).state is ProvisionState.RUNNING

    cancelled = await manager.cancel(initial.job_id)
    assert cancelled is not None
    assert cancelled.state is ProvisionState.CANCELLED
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None
    assert interrupted.is_set()
    assert initial.job_id not in manager._tasks

    again = await manager.cancel(initial.job_id)
    assert again is not None
    assert again.state is ProvisionState.CANCELLED
    assert initial.job_id not in manager._tasks

    started.clear()
    replacement = await manager.start(RuntimeKind.FASTER_WHISPER)
    assert replacement.job_id != initial.job_id
    await asyncio.wait_for(started.wait(), timeout=2)
    await manager.cancel(replacement.job_id)
    assert not manager._tasks


@pytest.mark.asyncio
async def test_shutdown_marks_inflight_install_cancelled_and_drops_task_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    started = asyncio.Event()
    was_cancelled = asyncio.Event()
    stop_calls = []

    async def fake_install(job_id: str) -> None:
        await manager._running(job_id, "installing")
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            was_cancelled.set()
            raise

    async def fake_stop_ollama() -> None:
        stop_calls.append("stopped")

    monkeypatch.setattr(manager, "_install_ollama", fake_install)
    monkeypatch.setattr(manager, "_stop_managed_ollama", fake_stop_ollama)
    job = await manager.start(RuntimeKind.OLLAMA)
    await asyncio.wait_for(started.wait(), timeout=2)

    await manager.shutdown()
    result = await manager.get_job(job.job_id)
    assert result is not None
    assert result.state is ProvisionState.CANCELLED
    assert result.finished_at is not None
    assert was_cancelled.is_set()
    assert manager._tasks == {}
    assert stop_calls == ["stopped"]


@pytest.mark.asyncio
async def test_cancelling_unknown_or_finished_job_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    assert await manager.cancel("does-not-exist") is None

    async def fake_install(job_id: str) -> None:
        await manager._complete(job_id, "already installed")

    monkeypatch.setattr(manager, "_install_ollama", fake_install)
    job = await manager.start(RuntimeKind.OLLAMA)
    for _ in range(100):
        status = await manager.get_job(job.job_id)
        if status and status.state is ProvisionState.COMPLETED:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("fake install did not complete")

    after = await manager.cancel(job.job_id)
    assert after is not None
    assert after.state is ProvisionState.COMPLETED
    assert job.job_id not in manager._tasks
