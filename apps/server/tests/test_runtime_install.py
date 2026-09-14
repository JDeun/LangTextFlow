import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from langtextflow.config import Settings
from langtextflow.runtime_install import (
    ProvisionState,
    RuntimeKind,
    RuntimeProvisionManager,
)
from langtextflow.storage_guard import StorageCapacityError


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
async def test_faster_whisper_provision_stops_before_install_when_storage_is_low(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: None if name == "faster_whisper" else real_find_spec(name),
    )
    monkeypatch.delattr(sys, "frozen", raising=False)

    def reject_storage(*_args, **_kwargs):
        raise StorageCapacityError("Not enough disk space to install runtime")

    async def must_not_run(_command: list[str]) -> None:
        raise AssertionError("installer must not run after storage guard failure")

    monkeypatch.setattr("langtextflow.runtime_install.ensure_storage_capacity", reject_storage)
    monkeypatch.setattr("langtextflow.runtime_install._run", must_not_run)
    manager = RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"), model_cache_dir=str(tmp_path / "cache")
        )
    )

    job = await manager.start(RuntimeKind.FASTER_WHISPER)
    settled = await _wait(manager, job.job_id)

    assert settled.state is ProvisionState.ERROR
    assert "Not enough disk space" in (settled.error or "")


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


@pytest.mark.asyncio
async def test_vibevoice_storage_guard_prevents_partial_provision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "langtextflow.runtime_install.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"git", "ffmpeg"} else None,
    )
    calls = 0

    def storage_check(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StorageCapacityError("Not enough disk space for VibeVoice model")
        return None

    async def must_not_run(_command: list[str]) -> None:
        raise AssertionError("provision command must not run after storage guard failure")

    monkeypatch.setattr("langtextflow.runtime_install.ensure_storage_capacity", storage_check)
    monkeypatch.setattr("langtextflow.runtime_install._run", must_not_run)
    manager = RuntimeProvisionManager(
        Settings(
            managed_runtime_dir=str(tmp_path / "runtime"),
            model_cache_dir=str(tmp_path / "cache"),
        )
    )

    job = await manager.start(RuntimeKind.VIBEVOICE)
    settled = await _wait(manager, job.job_id)

    assert settled.state is ProvisionState.ERROR
    assert "Not enough disk space" in (settled.error or "")
    assert not manager._vibevoice_ready_marker().exists()


@pytest.mark.asyncio
async def test_vibevoice_partial_install_is_not_reported_ready(tmp_path: Path) -> None:
    settings = Settings(
        managed_runtime_dir=str(tmp_path / "runtime"),
        model_cache_dir=str(tmp_path / "cache"),
    )
    manager = RuntimeProvisionManager(settings)
    repo = manager._vibevoice_repo()
    python = manager._vibevoice_python()
    (repo / "vllm_plugin").mkdir(parents=True)
    (repo / "vllm_plugin" / "asr_streaming_server.py").write_text("# stub", encoding="utf-8")
    python.parent.mkdir(parents=True)
    python.write_text("stub", encoding="utf-8")

    status = await manager.status(RuntimeKind.VIBEVOICE)
    assert status.installed is True
    assert status.available is False
    assert status.managed is False
    assert "partial install" in status.detail


@pytest.mark.asyncio
async def test_vibevoice_ready_manifest_must_match_pinned_revisions(tmp_path: Path) -> None:
    settings = Settings(
        managed_runtime_dir=str(tmp_path / "runtime"),
        model_cache_dir=str(tmp_path / "cache"),
    )
    manager = RuntimeProvisionManager(settings)
    repo = manager._vibevoice_repo()
    python = manager._vibevoice_python()
    model = manager._vibevoice_model_dir()
    (repo / "vllm_plugin").mkdir(parents=True)
    (repo / "vllm_plugin" / "asr_streaming_server.py").write_text("# stub", encoding="utf-8")
    python.parent.mkdir(parents=True)
    python.write_text("stub", encoding="utf-8")
    model.mkdir(parents=True)
    (model / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    (model / "added_tokens.json").write_text("{}", encoding="utf-8")
    marker = manager._vibevoice_ready_marker()
    marker.write_text(
        json.dumps(
            {
                "repo_ref": "wrong-ref",
                "model_id": settings.vibevoice_model_id,
                "model_revision": settings.vibevoice_model_revision,
            }
        ),
        encoding="utf-8",
    )

    status = await manager.status(RuntimeKind.VIBEVOICE)
    assert status.installed is True
    assert status.available is False
    assert "revisions changed" in status.detail

    manager._write_vibevoice_ready_marker(repo=repo, python=python, model_dir=model)
    status = await manager.status(RuntimeKind.VIBEVOICE)
    assert status.available is True
    assert status.managed is True
    assert status.detail == settings.vibevoice_repo_ref
