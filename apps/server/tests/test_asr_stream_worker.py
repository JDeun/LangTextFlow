from __future__ import annotations

import asyncio
import json
import os
import ssl
import struct
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from langtextflow.asr_stream_worker import StreamLeaf, read_pcm, validate
from langtextflow.inference_worker import validate_request as validate_whisper
from langtextflow.rust_runtime import _python_worker


def request(mode, **options):
    if mode in {"poll", "finish"}:
        options = {"sample_rate": 16000, "start_sample": 1600, "end_sample": 1600} | options
    return {
        "protocol_version": 1, "request_id": "stream-r1", "session_id": "session",
        "session_generation": 3, "segment_id": "candidate", "source_revision": 1,
        "task_kind": "asr", "source_language": "ja", "target_language": None,
        "deadline_ms": 1000, "payload": json.dumps({
            "stream_protocol_version": 1, "candidate_epoch": 2, "mode": mode, **options,
        }),
    }


@pytest.mark.parametrize("patch", [
    {"stream_protocol_version": True}, {"candidate_epoch": 0}, {"candidate_epoch": True},
    {"candidate_epoch": 2**53}, {"mode": "unknown"}, {"pcm": "raw audio"},
    {"endpoint": 1}, {"context_info": "r" * 49153},
])
def test_stream_subprotocol_is_strict_and_old_whisper_does_not_accept_it(patch):
    value = request("open", endpoint="http://127.0.0.1:8001", context_info="")
    with pytest.raises(ValueError):
        validate_whisper(value)
    value["payload"] = json.dumps(json.loads(value["payload"]) | patch)
    with pytest.raises((ValueError, TypeError)):
        validate(value)


@pytest.mark.parametrize("mode", ["wrong-size", "nan", "directory", "symlink"])
def test_stream_descriptor_requires_exact_regular_pcm_file(tmp_path, mode):
    path = tmp_path / "owned.pcm"
    path.write_bytes(struct.pack("<f", float("nan") if mode == "nan" else .25) * 4)
    if mode == "directory":
        path = tmp_path
    elif mode == "symlink":
        link = tmp_path / "link.pcm"
        try:
            link.symlink_to(path)
        except OSError:
            pytest.skip("symlink creation requires OS permission")
        path = link
    value = validate(request("append", pcm_path=str(path), start_sample=0,
                             end_sample=5 if mode == "wrong-size" else 4, sample_rate=48000))
    with pytest.raises((ValueError, OSError)):
        read_pcm(value)


async def test_stream_tls_uses_verified_explicit_trust_without_environment_overrides(monkeypatch):
    from test_vibevoice import FakeHttpClient, FakeWebSocket

    monkeypatch.setenv("SSL_CERT_FILE", "/missing-private-certificate")
    monkeypatch.setenv("SSL_CERT_DIR", "/missing-private-certificates")
    socket = FakeWebSocket()

    async def connect(url, **options):
        assert url == "wss://asr.example.com/v1/stream" and options["proxy"] is None
        assert options["ssl"].verify_mode == ssl.CERT_REQUIRED
        assert options["ssl"].check_hostname
        return socket

    monkeypatch.setattr("langtextflow.asr_stream_worker.httpx.AsyncClient", FakeHttpClient)
    monkeypatch.setattr("langtextflow.asr_stream_worker.NoRedirectConnect", connect)
    leaf = StreamLeaf()
    try:
        result = await leaf.handle(request(
            "open", endpoint="https://asr.example.com", context_info="",
        ))
        assert result["sample_rate"] == 16000
    finally:
        await leaf.close()


@asynccontextmanager
async def sidecar(*, rate=48000, fault=None):
    observed = {"context": None, "pcm": bytearray(), "connections": 0,
                "config_requested": asyncio.Event()}

    async def config(connection, incoming):
        if incoming.path == "/v1/config":
            observed["config_requested"].set()
            if fault == "config-hang":
                await asyncio.sleep(10)
            if fault == "config-redirect":
                return Response(302, "Found", Headers({"Location": "/secret-target"}), b"")
            body = json.dumps({"sample_rate": rate, "chunk_seconds": 2.}).encode()
            return Response(200, "OK", Headers({"Content-Type": "application/json"}), body)
        if fault == "ws-redirect":
            return Response(302, "Found", Headers({"Location": "/secret-target"}), b"")
        if incoming.path != "/v1/stream":
            raise AssertionError("redirect was followed")

    async def connection(socket):
        observed["connections"] += 1
        observed["context"] = json.loads(await socket.recv())
        async for frame in socket:
            if frame == "end":
                await socket.send(json.dumps({"text": "最後の文"}))
                await socket.send(json.dumps({"done": True}))
                return
            assert isinstance(frame, bytes)
            observed["pcm"].extend(frame)
            if fault == "disconnect":
                await socket.close()
                return
            if fault == "flood":
                for _ in range(129):
                    await socket.send(json.dumps({"text": "flood"}))
            elif fault == "surrogate":
                await socket.send('{"text":"\\ud800"}')
            else:
                await socket.send(json.dumps({"text": "日本語の原文"}))

    async with serve(connection, "127.0.0.1", 0, process_request=config) as server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", observed


@asynccontextmanager
async def worker():
    command = _python_worker("langtextflow.asr_stream_worker", [])
    process = await asyncio.create_subprocess_exec(
        command["program"], *command["args"], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
                pytest.fail("parent EOF did not stop owned stream leaf")
        assert await process.stderr.read() == b""


async def exchange(process, value):
    process.stdin.write((json.dumps(value) + "\n").encode())
    await process.stdin.drain()
    raw = await asyncio.wait_for(process.stdout.readline(), 3)
    assert len(raw) <= 128 * 1024
    reply = json.loads(raw)
    assert all(reply[k] == value[k] for k in (
        "request_id", "session_id", "session_generation", "segment_id", "source_revision",
    ))
    return reply


async def test_actual_stream_leaf_persistent_descriptor_batches_and_48k_clock(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    pcm = struct.pack("<f", -.5) * 1200
    path = tmp_path / "owned.pcm"
    path.write_bytes(pcm)
    async with sidecar() as (endpoint, seen), worker() as process:
        opened = await exchange(process, request("open", endpoint=endpoint, context_info="題名"))
        assert opened["status"] == "ok"
        assert json.loads(opened["result"])["sample_rate"] == 48000
        texts = []
        for i in range(2):
            reply = await exchange(process, request(
                "append", pcm_path=str(path), start_sample=i * 1200,
                end_sample=(i + 1) * 1200, sample_rate=48000,
            ))
            result = json.loads(reply["result"])
            assert result["transmitted_samples"] == (i + 1) * 1200
            texts.extend(result["messages"])
        tail = {"sample_rate": 48000, "start_sample": 2400, "end_sample": 2400}
        finished = json.loads((await exchange(process, request("finish", **tail)))["result"])
        texts.extend(finished["messages"])
        for _ in range(100):
            if finished["done"]:
                break
            await asyncio.sleep(.01)
            finished = json.loads((await exchange(process, request("poll", **tail)))["result"])
            texts.extend(finished["messages"])
        assert finished["done"]
        assert [m["text"] for m in texts] == ["日本語の原文", "日本語の原文", "最後の文"]
        assert all(0 < m["audio_end_sample"] <= 2400 for m in texts)
        assert seen["connections"] == 1 and seen["pcm"] == pcm * 2
        assert seen["context"]["context_info"] == "題名"
        assert "segment_id" not in finished and "timestamp_basis" not in finished


@pytest.mark.parametrize("fault", ["config-redirect", "ws-redirect", "disconnect", "flood",
                                        "surrogate", "stale-epoch", "wrong-offset"])
async def test_actual_stream_leaf_bounds_failure_and_redacts_endpoint_path(tmp_path, fault):
    path = tmp_path / "private-audio.pcm"
    path.write_bytes(b"\0" * 6400)
    async with sidecar(rate=16000, fault=fault) as (endpoint, _), worker() as process:
        reply = await exchange(process, request("open", endpoint=endpoint, context_info=""))
        if reply["status"] == "ok":
            value = request("append", pcm_path=str(path), start_sample=0,
                            end_sample=1600, sample_rate=16000)
            if fault == "stale-epoch":
                value["payload"] = json.dumps(json.loads(value["payload"]) | {"candidate_epoch": 1})
            elif fault == "wrong-offset":
                value["payload"] = json.dumps(json.loads(value["payload"]) | {"start_sample": 1})
            reply = await exchange(process, value)
            if reply["status"] == "ok":
                await asyncio.sleep(.1)
                reply = await exchange(process, request("poll"))
        assert reply["status"] == "error" and reply["error"] == "asr_stream_failed"
        assert reply["result"] is None
        assert endpoint not in json.dumps(reply) and str(path) not in json.dumps(reply)
        assert await asyncio.wait_for(process.wait(), 3) == 1


async def test_actual_stream_parent_eof_interrupts_network_startup_without_reply():
    async with sidecar(fault="config-hang") as (endpoint, seen), worker() as process:
        value = request("open", endpoint=endpoint, context_info="")
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()
        await asyncio.wait_for(seen["config_requested"].wait(), 3)
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 3) == 0
        assert await process.stdout.read() == b""
