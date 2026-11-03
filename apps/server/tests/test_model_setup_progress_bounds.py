"""Real in-memory HTTP streams exercise bounded Ollama NDJSON and job lifecycle."""
from __future__ import annotations

import asyncio
import gzip
import json

import httpx
import pytest

from langtextflow.config import Settings
from langtextflow.model_setup import ModelSetupManager, SetupJobState
from langtextflow.provider_http import MAX_PROVIDER_RESPONSE_BYTES, READ_CHUNK_BYTES


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.received = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.received += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


async def finish(manager, job):
    await asyncio.wait_for(manager._tasks[job.job_id], 2)
    assert job.job_id not in manager._tasks
    assert not manager._active_keys
    return await manager.get_job(job.job_id)


def manager_for(handler):
    return ModelSetupManager(
        Settings(ollama_url="https://user:password@ollama.invalid/?key=secret"),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_no_newline_eight_mib_progress_line_is_not_drained_and_job_can_retry():
    body = Body([b'{"status":"', *([b"x" * 65536] * 128), b'"}'])
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["accept-encoding"] == "identity"
        assert request.extensions["timeout"]["read"] is None
        if len(requests) > 1:
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(200, stream=body)

    manager = manager_for(respond)
    job = await manager.start_ollama_pull("fixture")
    failed = await finish(manager, job)
    assert failed.state is SetupJobState.ERROR and failed.finished_at is not None
    assert failed.error == "provider progress line exceeds the safety byte limit"
    assert body.closed and MAX_PROVIDER_RESPONSE_BYTES < body.received < 1024 * 1024
    retry = await manager.start_ollama_pull("fixture")
    assert retry.job_id != job.job_id
    assert (await finish(manager, retry)).state is SetupJobState.COMPLETED


@pytest.mark.asyncio
async def test_gzip_bomb_is_rejected_before_reading_or_expanding():
    body = Body([gzip.compress(b"x" * (8 * 1024 * 1024))])
    manager = manager_for(lambda _: httpx.Response(
        200, stream=body, headers={"Content-Encoding": "gzip"},
    ))
    job = await manager.start_ollama_pull("fixture")
    failed = await finish(manager, job)
    assert failed.state is SetupJobState.ERROR
    assert failed.error == "provider response must use identity content encoding"
    assert body.closed and body.received == 0


@pytest.mark.asyncio
async def test_limit_is_per_line_without_total_stream_or_download_size_cap():
    total = 5 * 1024**3
    prefix = json.dumps({
        "status": "downloading", "completed": total // 2, "total": total,
    }).encode()[:-1] + b',"padding":"'
    line = prefix + b"x" * (MAX_PROVIDER_RESPONSE_BYTES - len(prefix) - 2) + b'"}'
    assert len(line) == MAX_PROVIDER_RESPONSE_BYTES
    data = line + b"\n" + line + b"\n" + json.dumps({
        "status": "success", "completed": total, "total": total,
    }).encode()
    body = Body(data[start:start + 65536] for start in range(0, len(data), 65536))
    manager = manager_for(lambda _: httpx.Response(
        200, stream=body, headers={"Content-Length": str(len(data))},
    ))
    job = await manager.start_ollama_pull("fixture")
    completed = await finish(manager, job)
    assert completed.state is SetupJobState.COMPLETED and completed.progress_percent == 100
    assert completed.total_bytes == completed.completed_bytes == total
    assert body.closed and body.received == len(data) > 2 * MAX_PROVIDER_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_whitespace_crlf_split_utf8_and_final_line_without_newline(monkeypatch):
    lines = [
        {"status": "내려받는 중 😀", "total": 100, "completed": 40},
        {"status": "success", "digest": "sha256:완료😀", "total": 100, "completed": 100},
    ]
    data = b" \t\r\n\n" + b"\r\n".join(
        json.dumps(line, ensure_ascii=False).encode() for line in lines
    )
    body = Body(bytes([byte]) for byte in data)
    manager = manager_for(lambda _: httpx.Response(200, stream=body))
    received = []
    original = manager._apply_ollama_progress

    async def record(job_id, payload):
        received.append(payload)
        await original(job_id, payload)

    monkeypatch.setattr(manager, "_apply_ollama_progress", record)
    job = await manager.start_ollama_pull("fixture")
    completed = await finish(manager, job)
    assert completed.state is SetupJobState.COMPLETED and completed.completed_bytes == 100
    assert received == lines and body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    b"", b" \r\n\n",
    b'{"status":"downloading","completed":40,"total":100}\n',
    b'{"status":"verifying sha256 digest","completed":100,"total":100}\n',
    b'{"status":"success"}\n{"status":"downloading"}\n',
])
async def test_eof_without_terminal_success_never_marks_download_completed(data):
    body = Body([data])
    manager = manager_for(lambda _: httpx.Response(200, stream=body))
    job = await manager.start_ollama_pull("fixture")
    failed = await finish(manager, job)
    assert failed.state is SetupJobState.ERROR and failed.finished_at is not None
    assert failed.error == "Ollama download stream ended before success"
    if b'"completed":40' in data:
        assert failed.progress_percent == 40 and failed.completed_bytes == 40
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [307, 500, "provider-error", "invalid-utf8"])
async def test_server_errors_are_terminal_and_do_not_expose_credentials_or_body(failure):
    first_line = (b'{"error":"private body https://user:password@server/?key=secret"}\n'
                  if failure != "invalid-utf8" else b'{"status":"private\xff"}\n')
    body = Body([first_line, b"unread trailing response" * 1000])
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            failure if isinstance(failure, int) else 200,
            headers={"Location": "https://other.invalid"}, stream=body,
        )

    manager = manager_for(respond)
    job = await manager.start_ollama_pull("fixture")
    failed = await finish(manager, job)
    assert failed.state is SetupJobState.ERROR and failed.finished_at is not None
    assert body.closed and len(requests) == 1
    assert body.received <= len(first_line)
    for secret in ["private", "password", "secret", "https://", "user:"]:
        assert secret not in failed.error
    if isinstance(failure, int):
        assert failed.error == f"provider returned HTTP {failure}"
        assert body.received == 0
    elif failure == "provider-error":
        assert failed.error == "Ollama download provider reported an error"
    else:
        assert failed.error == "provider progress line is not valid UTF-8"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "shutdown"])
async def test_small_progress_is_immediate_and_interrupted_partial_line_releases_job(action):
    waiting = asyncio.Event()
    closed = asyncio.Event()
    calls = 0

    class PartialBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"status":"downloading","completed":5,"total":100}\n'
            yield b"x" * READ_CHUNK_BYTES
            waiting.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def respond(request):
        nonlocal calls
        calls += 1
        return (httpx.Response(200, stream=PartialBody()) if calls == 1
                else httpx.Response(200, json={"status": "success"}))

    manager = manager_for(respond)
    job = await manager.start_ollama_pull("fixture")
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        progress = await manager.get_job(job.job_id)
        assert progress.state is SetupJobState.RUNNING and progress.completed_bytes == 5
        duplicate = await manager.start_ollama_pull("fixture")
        assert duplicate.job_id == job.job_id and calls == 1
        if action == "cancel":
            await manager.cancel_job(job.job_id)
        else:
            await manager.shutdown()
        final = await manager.get_job(job.job_id)
        assert final.state is SetupJobState.CANCELLED and final.finished_at is not None
        assert closed.is_set() and not manager._active_keys and not manager._tasks
        retry = await manager.start_ollama_pull("fixture")
        assert retry.job_id != job.job_id
        assert (await finish(manager, retry)).state is SetupJobState.COMPLETED
    finally:
        await manager.shutdown()
