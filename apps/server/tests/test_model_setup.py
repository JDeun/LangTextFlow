from __future__ import annotations

import asyncio

import httpx
import pytest

from langtextflow.config import Settings
from langtextflow.model_setup import (
    ModelSetupManager,
    SetupJobState,
    _normalize_model_name,
    _parse_progress_line,
    _whisper_download_estimate,
)
from langtextflow.storage_guard import StorageCapacityError


async def wait_for_terminal(manager: ModelSetupManager, job_id: str) -> None:
    for _ in range(100):
        job = await manager.get_job(job_id)
        assert job is not None
        if job.state in {
            SetupJobState.COMPLETED,
            SetupJobState.CANCELLED,
            SetupJobState.ERROR,
        }:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("model setup job did not finish")


def test_parse_progress_line_rejects_invalid_json() -> None:
    assert _parse_progress_line("not-json") is None
    assert _parse_progress_line("[]") is None
    assert _parse_progress_line('{"status":"pulling manifest"}') == {
        "status": "pulling manifest"
    }


def test_model_name_validation_is_conservative() -> None:
    assert _normalize_model_name("translategemma:4b") == "translategemma:4b"
    assert _normalize_model_name("namespace/model:tag") == "namespace/model:tag"
    with pytest.raises(ValueError):
        _normalize_model_name("../bad model")


def test_whisper_download_estimate_is_conservative_for_unknown_models() -> None:
    assert _whisper_download_estimate("small") < _whisper_download_estimate("unknown-model")
    assert _whisper_download_estimate("namespace/large-v3") == 6 * 1024**3


@pytest.mark.asyncio
async def test_local_ollama_pull_is_blocked_when_storage_reserve_is_unavailable(
    monkeypatch,
) -> None:
    def reject(*_args, **_kwargs):
        raise StorageCapacityError("Not enough disk space")

    monkeypatch.setattr("langtextflow.model_setup.ensure_storage_capacity", reject)
    manager = ModelSetupManager(Settings(ollama_url="http://127.0.0.1:11434"))

    with pytest.raises(StorageCapacityError, match="Not enough disk space"):
        await manager.start_ollama_pull("translategemma:4b")

    assert await manager.list_jobs() == []


@pytest.mark.asyncio
async def test_ollama_pull_tracks_stream_progress() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        body = (
            b'{"status":"pulling manifest"}\n'
            b'{"status":"downloading","digest":"sha256:test",'
            b'"total":100,"completed":40}\n'
            b'{"status":"downloading","digest":"sha256:test",'
            b'"total":100,"completed":100}\n'
            b'{"status":"success"}\n'
        )
        return httpx.Response(200, content=body)

    manager = ModelSetupManager(
        Settings(ollama_url="http://ollama.test"),
        transport=httpx.MockTransport(handler),
    )
    job = await manager.start_ollama_pull("translategemma:4b")
    await wait_for_terminal(manager, job.job_id)

    completed = await manager.get_job(job.job_id)
    assert completed is not None
    assert completed.state is SetupJobState.COMPLETED
    assert completed.progress_percent == 100.0
    assert completed.total_bytes == 100
    assert completed.completed_bytes == 100
    assert completed.digest == "sha256:test"
    assert completed.finished_at is not None


@pytest.mark.asyncio
async def test_duplicate_active_pull_returns_same_job() -> None:
    gate = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"status":"pulling manifest"}\n'
            await gate.wait()
            yield b'{"status":"success"}\n'

        async def aclose(self) -> None:
            gate.set()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=WaitingStream())

    manager = ModelSetupManager(
        Settings(ollama_url="http://ollama.test"),
        transport=httpx.MockTransport(handler),
    )
    first = await manager.start_ollama_pull("translategemma:4b")
    second = await manager.start_ollama_pull("translategemma:4b")

    assert second.job_id == first.job_id
    await manager.cancel_job(first.job_id)
    cancelled = await manager.get_job(first.job_id)
    assert cancelled is not None
    assert cancelled.state is SetupJobState.CANCELLED
