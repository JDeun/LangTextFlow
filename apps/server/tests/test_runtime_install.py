import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from langtextflow.config import Settings
from langtextflow.runtime_install import (
    ProvisionState,
    RuntimeKind,
    RuntimeProvisionManager,
)


async def _wait(manager: RuntimeProvisionManager, job_id: str):
    for _ in range(100):
        job = await manager.get_job(job_id)
        assert job is not None
        if job.state in {ProvisionState.COMPLETED, ProvisionState.ERROR, ProvisionState.CANCELLED}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("runtime provision job did not settle")


@pytest.mark.asyncio
async def test_faster_whisper_existing_runtime_completes_without_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name == "faster_whisper" else real_find_spec(name),
    )
    manager = RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"), model_cache_dir=str(tmp_path / "cache")
        )
    )
    job = await manager.start(RuntimeKind.FASTER_WHISPER)
    settled = await _wait(manager, job.job_id)
    assert settled.state is ProvisionState.COMPLETED


@pytest.mark.asyncio
async def test_ollama_install_fails_closed_without_supported_package_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("langtextflow.runtime_install.shutil.which", lambda _name: None)
    monkeypatch.setattr(sys, "platform", "linux")
    manager = RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"), model_cache_dir=str(tmp_path / "cache")
        )
    )
    job = await manager.start(RuntimeKind.OLLAMA)
    settled = await _wait(manager, job.job_id)
    assert settled.state is ProvisionState.ERROR
    assert "winget" in (settled.error or "")


@pytest.mark.asyncio
async def test_ollama_status_distinguishes_installed_from_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "ollama"
    executable.write_text("stub", encoding="utf-8")
    manager = RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"), model_cache_dir=str(tmp_path / "cache")
        )
    )
    monkeypatch.setattr(manager, "_find_ollama_executable", lambda: executable)

    async def unhealthy() -> bool:
        return False

    monkeypatch.setattr(manager, "_ollama_healthy", unhealthy)
    status = await manager.status(RuntimeKind.OLLAMA)
    assert status.installed
    assert not status.available
    assert status.detail == "installed, not running"


@pytest.mark.asyncio
async def test_ollama_existing_healthy_runtime_completes_without_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "ollama"
    executable.write_text("stub", encoding="utf-8")
    manager = RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"), model_cache_dir=str(tmp_path / "cache")
        )
    )
    monkeypatch.setattr(manager, "_find_ollama_executable", lambda: executable)

    async def healthy() -> bool:
        return True

    monkeypatch.setattr(manager, "_ollama_healthy", healthy)
    job = await manager.start(RuntimeKind.OLLAMA)
    settled = await _wait(manager, job.job_id)
    assert settled.state is ProvisionState.COMPLETED
    assert settled.status == "Ollama is running"


@pytest.mark.asyncio
async def test_vibevoice_status_uses_managed_runtime_paths(tmp_path: Path) -> None:
    settings = Settings(
        managed_runtime_dir=str(tmp_path / "runtime"),
        model_cache_dir=str(tmp_path / "cache"),
    )
    manager = RuntimeProvisionManager(settings)
    status = await manager.status(RuntimeKind.VIBEVOICE)
    assert not status.available
    assert not status.installed
    assert status.path is None
