"""Real owned HTTP/OS contracts; no external endpoint, model or native library."""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from langtextflow.config import Settings
from langtextflow.models import SessionContext, StartSessionRequest
from langtextflow.pipeline import CaptionPipeline
from langtextflow.rust_runtime import _source_context
from langtextflow.translation.factory import create_translator, provider_options
from langtextflow.translation_worker import validate_request
from langtextflow.worker_ipc import IDENTITY_KEYS


def request(options, mode="translate", text="원문", context=None, serial=1):
    return {"protocol_version": 1, "request_id": f"r{serial}", "session_id": "leaf",
            "session_generation": 1, "segment_id": "s", "source_revision": 1,
            "task_kind": "translation", "source_language": "ko", "target_language": "fr",
            "deadline_ms": 1000, "payload": json.dumps({
                "mode": mode, "text": text, "context": context or {}, "options": options,
            })}


@pytest.mark.parametrize("provider", ["none", "demo", "ollama", "openai-compatible", "m2m100-ct2"])
def test_pipeline_and_leaf_share_provider_construction(provider):
    settings = Settings(_env_file=None)
    req = StartSessionRequest(translation_provider=provider, translation_model="configured:tag")
    expected = CaptionPipeline(None, settings)._build_translator(req)
    actual = create_translator(provider_options(settings, req))
    assert type(expected) is type(actual)
    if actual is not None:
        assert expected.provider == actual.provider and expected.model == actual.model
        for field in ("base_url", "api_key", "request_timeout_seconds", "model_dir",
                      "tokenizer_dir", "cpu_threads"):
            assert getattr(expected, field, None) == getattr(actual, field, None)


def test_leaf_context_preserves_full_mt_prefix_without_new_schema_fields():
    options = {"provider": "demo", "url": "", "model": "deterministic-demo",
               "api_key": "", "timeout_seconds": 1}
    context = SessionContext(title="session", reference_text="x" * 5000).model_dump(
        include={"title", "presenter", "preset", "glossary", "reference_text"}, mode="json",
    )
    resolved = validate_request(request(options, context=context))[2]
    assert resolved.reference_excerpt(5000) == "x" * 5000
    with pytest.raises(ValueError):
        validate_request(request({**options, "api_key": "x" * 8193}))
    with pytest.raises(ValueError):
        validate_request({**request(options), "source_language": "unknown"})


def test_one_shared_snapshot_preserves_exact_existing_asr_correction_and_mt_prompts():
    from langtextflow.asr.faster_whisper import FasterWhisperStreamingAsrEngine
    from langtextflow.llm_correction import OllamaConstrainedCorrector
    from langtextflow.translation import OllamaTranslator, OpenAICompatibleTranslator

    original = StartSessionRequest(context=SessionContext(
        title="Title", presenter="Presenter", description="d" * 4000,
        reference_text="r" * 1200 + "c" * 1800 + "m" * 2000 + "unused" * 1000,
    ))
    snapshot = SessionContext.model_validate(_source_context(original))
    captured = []
    engine = FasterWhisperStreamingAsrEngine(lambda _: None)

    def transcribe(audio, **kwargs):
        captured.append(kwargs)
        return [], SimpleNamespace()

    engine._model = SimpleNamespace(transcribe=transcribe)
    for context in (original.context, snapshot):
        engine._transcribe_chunk_sync(struct.pack("<f", .1) * 160, StartSessionRequest(
            context=context,
        ))
    assert captured[0] == captured[1]
    assert "r" * 1200 in captured[0]["initial_prompt"]
    assert "c" * 100 not in captured[0]["initial_prompt"]
    for build in (OllamaConstrainedCorrector.build_messages,):
        assert build("원문", source_language="ko", context=original.context) == build(
            "원문", source_language="ko", context=snapshot,
        )
    for translator in (OllamaTranslator, OpenAICompatibleTranslator):
        assert translator.build_messages(
            "원문", source_language="ko", target_language="en", context=original.context,
        ) == translator.build_messages(
            "원문", source_language="ko", target_language="en", context=snapshot,
        )


@pytest.fixture
def mt_endpoint():
    records = []
    state = {"unavailable": True, "prepare_delay": 0., "delay": 0., "gets": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["gets"] += 1
            time.sleep(state["prepare_delay"])
            if state["unavailable"]:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(json.dumps({"models": [{"name": "configured:tag"}],
                                             "data": [{"id": "configured:tag"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append((body, self.headers.get("Authorization")))
            time.sleep(state["delay"])
            output = "" if len(records) == 1 else "traduction française"
            self.send_response(200)
            self.end_headers()
            payload = {"message": {"content": output},
                       "choices": [{"message": {"content": output}}]}
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", records, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai-compatible", "demo"])
async def test_actual_leaf_prepare_failure_request_rejection_and_next_success_reuse_pid(
    provider, mt_endpoint,
):
    endpoint, records, state = mt_endpoint
    options = {"provider": provider, "url": endpoint,
               "model": "configured:tag", "api_key": "owned-synthetic-key", "timeout_seconds": 1}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "langtextflow.translation_worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        pid = process.pid

        async def send(mode, text="원문", serial=1):
            frame = request(options, mode, text, serial=serial)
            process.stdin.write((json.dumps(frame) + "\n").encode())
            await process.stdin.drain()
            reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
            assert {key: reply[key] for key in IDENTITY_KEYS} == {
                key: frame[key] for key in IDENTITY_KEYS}
            assert process.pid == pid and process.returncode is None
            assert "owned-synthetic-key" not in json.dumps(reply)
            assert endpoint not in json.dumps(reply)
            return reply

        first = await send("prepare")
        expected = None if provider == "demo" else "translation_provider_unavailable"
        assert first["error"] == expected
        state["unavailable"] = False
        assert (await send("prepare", serial=2))["result"] == "ready"
        rejected = await send("translate", serial=3)
        if provider == "demo":
            assert rejected["result"] == "[FR demo] 원문"
        else:
            assert rejected["error"] == "translation_rejected" and rejected["result"] is None
        assert (await send("translate", serial=4))["error"] is None
        if provider != "demo":
            assert len(records) == 2
            assert records[0][1] == ("Bearer owned-synthetic-key" if provider == "openai-compatible"
                                     else None)
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 5) == 0
        assert await process.stderr.read() == b""
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
