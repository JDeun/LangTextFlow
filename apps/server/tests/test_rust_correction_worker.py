"""Actual correction child and owned HTTP fixture; no model/quality claims."""
from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from langtextflow.correction_worker import validate_request
from langtextflow.rust_runtime import _python_worker
from langtextflow.worker_ipc import IDENTITY_KEYS


def request(mode="deterministic", text="요한 보금과 성녕", source="ko", context=None, url=""):
    return {
        "protocol_version": 1, "request_id": "correction", "session_id": "session",
        "session_generation": 1, "segment_id": "segment", "source_revision": 1,
        "task_kind": "correction", "source_language": source, "target_language": None,
        "deadline_ms": 1000, "payload": json.dumps({
            "mode": mode, "text": text, "context": context or {"preset": "church"},
            "options": {"provider": "ollama" if url else "none", "url": url,
                        "model": "fixture-model:tag", "timeout_seconds": 1.},
        }),
    }


@pytest.mark.parametrize("change", [
    {"target_language": "en"}, {"task_kind": "asr"}, {"source_language": "auto"},
    {"session_generation": True}, {"payload": "{}"},
])
def test_correction_contract_refuses_invalid_requests_before_provider_access(change):
    with pytest.raises(ValueError):
        validate_request(request() | change)


@pytest.mark.parametrize("change", [
    {"mode": []}, {"mode": "unknown"}, {"context": {"private-extra": "ignored?"}},
    {"options": {}}, {"text": ""}, {"text": "x" * 20_001},
], ids=["mode-type", "mode-unknown", "context-fields", "options", "empty", "text-cap"])
def test_correction_payload_refuses_invalid_data(change):
    value = request()
    value["payload"] = json.dumps(json.loads(value["payload"]) | change)
    with pytest.raises(ValueError):
        validate_request(value)


async def child():
    command = _python_worker("langtextflow.correction_worker", [])
    return await asyncio.create_subprocess_exec(
        command["program"], *command["args"], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )


async def exchange(process, value):
    process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
    await process.stdin.drain()
    result = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
    assert {key: result[key] for key in IDENTITY_KEYS} == {key: value[key] for key in IDENTITY_KEYS}
    return result


async def close(process):
    if process.returncode is None:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            process.kill()
            await process.wait()


async def test_actual_child_reuses_existing_deterministic_corrector_and_source_scope():
    process = await child()
    try:
        cases = [
            ("ko", "요한 보금과 성녕", "요한복음과 성령", []),
            ("en", "Spirit of God", "Holy Spirit", [
                {"term": "Holy Spirit", "source_language": "en", "aliases": ["Spirit of God"]},
            ]),
            ("ja", "せいれい", "聖霊", [
                {"term": "聖霊", "source_language": "ja", "aliases": ["せいれい"]},
            ]),
        ]
        for source, text, expected, glossary in cases:
            context = {"preset": "church", "glossary": [*glossary,
                {"term": "WRONG", "source_language": "en" if source != "en" else "ja",
                 "aliases": [text]},
                {"term": "WRONG", "source_language": source, "aliases": [text],
                 "presets": ["conference"]},
            ]}
            reply = await exchange(process, request(text=text, source=source, context=context))
            assert reply["status"] == "ok" and reply["result"] == expected
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stderr.read() == b""
    finally:
        await close(process)


@pytest.fixture
def ollama():
    calls = []
    result = {"candidate": "요한복음 3장.", "available": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            payload = {"models": [{"name": "fixture-model:tag"}] if result["available"] else []}
            self.reply(payload)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            self.reply({"message": {"content": json.dumps({
                "corrected_text": result["candidate"],
            })}})

        def reply(self, payload):
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


async def test_actual_ollama_leaf_reuses_prompt_safety_numeric_guard_and_private_errors(ollama):
    url, calls, result = ollama
    process = await child()
    context = {"title": "Synthetic title", "presenter": "Presenter", "preset": "church",
               "reference_text": "r" * 3500, "glossary": [
                   {"term": "옳은 용어", "source_language": "ko", "aliases": ["용어"]},
                   {"term": "WRONG_SOURCE", "source_language": "en"},
               ]}
    try:
        assert (await exchange(process, request("prepare", url=url)))["result"] == "ready"
        value = request("llm", "요한복음 3장", context=context, url=url)
        assert (await exchange(process, value))["result"] == "요한복음 3장."
        prompt = json.loads(calls[0]["messages"][1]["content"])
        assert prompt["session_title"] == "Synthetic title" and prompt["presenter"] == "Presenter"
        assert prompt["reference_material"] == "r" * 3000
        assert prompt["terminology_hints"] == [{"canonical": "옳은 용어", "aliases": ["용어"]}]
        result["candidate"] = "요한복음 9장."
        rejected = await exchange(process, value)
        assert rejected["status"] == "error" and rejected["error"] == "correction_rejected"
        assert url not in json.dumps(rejected) and "9장" not in json.dumps(rejected)
        result["candidate"] = "요한복음 3장."
        assert (await exchange(process, value))["result"] == "요한복음 3장."
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stderr.read() == b""
    finally:
        await close(process)


async def test_unavailable_optional_provider_keeps_deterministic_child_usable(ollama):
    url, _, result = ollama
    result["available"] = False
    process = await child()
    try:
        reply = await exchange(process, request("prepare", url=url))
        assert reply["error"] == "correction_provider_unavailable"
        reply = await exchange(process, request(url=url))
        assert reply["result"] == "요한복음과 성령"
    finally:
        await close(process)
