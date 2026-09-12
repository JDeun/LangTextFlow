from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from langtextflow.config import Settings
from langtextflow.vibevoice_lifecycle import (
    VibeVoiceLifecycleManager,
    VibeVoiceLifecycleMode,
)


class FakeProcess:
    def __init__(self, on_stop=None) -> None:
        self.pid = 4242
        self.returncode = None
        self.stdout = None
        self.terminated = False
        self.killed = False
        self._on_stop = on_stop

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0
        if self._on_stop is not None:
            self._on_stop()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        if self._on_stop is not None:
            self._on_stop()

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


def prepared_settings(tmp_path: Path, **updates) -> Settings:
    repo = tmp_path / "VibeVoice"
    module = repo / "vllm_plugin" / "asr_streaming_server.py"
    module.parent.mkdir(parents=True)
    module.write_text("# prepared runtime\n", encoding="utf-8")

    model = tmp_path / "vibevoice-streaming"
    model.mkdir()
    (model / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    (model / "added_tokens.json").write_text(
        json.dumps({"<|text_chunk_end|>": 151665}),
        encoding="utf-8",
    )
    values = {
        "vibevoice_url": "http://127.0.0.1:8001",
        "vibevoice_repo_path": str(repo),
        "vibevoice_model_path": str(model),
        "vibevoice_startup_timeout_seconds": 1.0,
    }
    values.update(updates)
    return Settings(**values)


def test_build_command_uses_prepared_runtime_without_installer(tmp_path: Path) -> None:
    manager = VibeVoiceLifecycleManager(prepared_settings(tmp_path))

    command, cwd, _ = manager._build_command()

    joined = " ".join(command)
    assert "vllm_plugin.asr_streaming_server" in joined
    assert "--port 8001" in joined
    assert "--model" in command
    assert "pip" not in joined
    assert "start_streaming_server.py" not in joined
    assert cwd.endswith("VibeVoice")


@pytest.mark.asyncio
async def test_external_healthy_sidecar_is_never_claimed_or_stopped(tmp_path: Path) -> None:
    spawned = False

    async def probe() -> bool:
        return True

    async def spawn(command, cwd, env):
        nonlocal spawned
        spawned = True
        raise AssertionError("external sidecar must not spawn a managed process")

    manager = VibeVoiceLifecycleManager(
        prepared_settings(tmp_path),
        probe=probe,
        spawn=spawn,
    )

    started = await manager.start()
    stopped = await manager.stop()

    assert not spawned
    assert started.mode is VibeVoiceLifecycleMode.EXTERNAL
    assert started.healthy is True
    assert started.managed is False
    assert stopped.mode is VibeVoiceLifecycleMode.EXTERNAL
    assert stopped.managed is False
    assert "종료하지 않았습니다" in stopped.status


@pytest.mark.asyncio
async def test_managed_start_and_stop_owns_only_spawned_process(tmp_path: Path) -> None:
    healthy = False
    spawned = asyncio.Event()

    def mark_stopped() -> None:
        nonlocal healthy
        healthy = False

    process = FakeProcess(on_stop=mark_stopped)

    async def probe() -> bool:
        return healthy

    async def spawn(command, cwd, env):
        spawned.set()
        return process

    manager = VibeVoiceLifecycleManager(
        prepared_settings(tmp_path),
        probe=probe,
        spawn=spawn,
    )

    starting = await manager.start()
    assert starting.mode is VibeVoiceLifecycleMode.STARTING
    await spawned.wait()
    healthy = True

    for _ in range(10):
        await asyncio.sleep(0)
        current = await manager.status()
        if current.healthy:
            break
    else:
        raise AssertionError("managed sidecar did not become healthy")

    assert current.mode is VibeVoiceLifecycleMode.MANAGED
    assert current.pid == 4242
    assert current.managed is True

    stopped = await manager.stop()
    assert process.terminated is True
    assert stopped.mode is VibeVoiceLifecycleMode.STOPPED
    assert stopped.running is False


@pytest.mark.asyncio
async def test_managed_start_rejects_non_loopback_url(tmp_path: Path) -> None:
    manager = VibeVoiceLifecycleManager(
        prepared_settings(tmp_path, vibevoice_url="http://192.168.0.10:8001"),
        probe=lambda: asyncio.sleep(0, result=False),
    )

    with pytest.raises(ValueError, match="loopback"):
        await manager.start()


@pytest.mark.asyncio
async def test_missing_streaming_tokenizer_is_unconfigured(tmp_path: Path) -> None:
    settings = prepared_settings(tmp_path)
    Path(settings.vibevoice_model_path, "added_tokens.json").unlink()
    manager = VibeVoiceLifecycleManager(
        settings,
        probe=lambda: asyncio.sleep(0, result=False),
    )

    state = await manager.status()

    assert state.mode is VibeVoiceLifecycleMode.UNCONFIGURED
    assert state.configured is False
    assert "tokenizer" in (state.error or "")
