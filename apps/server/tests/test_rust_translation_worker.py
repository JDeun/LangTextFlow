"""Shipped worker + native adapter with synthetic external model libraries only."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from langtextflow.translation.m2m100_worker import validate_rust_request
from langtextflow.worker_ipc import IDENTITY_KEYS, MAX_FRAME_BYTES


def request(source="ko", target="en", *, text="원문", context=None):
    return {
        "protocol_version": 1, "request_id": "r1", "session_id": "s1",
        "session_generation": 2, "segment_id": "rs-1-0", "source_revision": 3,
        "task_kind": "translation", "source_language": source, "target_language": target,
        "deadline_ms": 1000,
        "payload": json.dumps({"text": text, "context": context or {}}, ensure_ascii=False),
    }


@pytest.mark.parametrize("patch", [
    {"protocol_version": True}, {"session_generation": True}, {"source_revision": -1},
    {"deadline_ms": 120_001}, {"task_kind": "asr"}, {"target_language": None},
    {"target_language": "ko"}, {"source_language": []}, {"target_language": []},
    {"payload": "{}"}, {"payload": json.dumps({"text": " ", "context": {}})},
    {"payload": json.dumps({"text": "a" * 20_001, "context": {}})},
    {"payload": json.dumps({"text": "source", "context": {"reference_text": "unneeded"}})},
    {"unexpected": 1},
])
def test_contract_rejects_invalid_or_unneeded_context_before_native_load(patch):
    with pytest.raises(ValueError):
        validate_rust_request({**request(), **patch})


def child_command(tmp_path, mode="normal", *, legacy=False):
    marker = tmp_path / "native-stage"
    for name in ("model.bin", "config.json", "tokenizer_config.json",
                 "sentencepiece.bpe.model", "vocab.json"):
        if mode != "missing-cache" or name != "model.bin":
            (tmp_path / name).touch()
    fixture = tmp_path / "native_fixture.py"
    fixture.write_text(f'''
import os, sys, time, types
from pathlib import Path
marker = Path({str(marker)!r})
mode = {mode!r}
class Tokenizer:
    lang_code_to_token = {{lang: "__" + lang + "__" for lang in ("ko", "en", "ja")}}
    @classmethod
    def from_pretrained(cls, path, **kwargs):
        assert kwargs == {{"local_files_only": True}}
        assert os.environ["USE_TORCH"] == "0"
        return cls()
    def encode(self, text): return [text]
    def convert_ids_to_tokens(self, values): return values
    def convert_tokens_to_ids(self, values): return values
    def decode(self, values, **kwargs): return " ".join(values)
class Native:
    def __init__(self, path, **kwargs):
        assert kwargs == {{"device":"cpu", "compute_type":"int8", "inter_threads":1,
                          "intra_threads":2}}
        with marker.open("a") as out: out.write("load\\n")
        if mode == "load-hang": time.sleep(120)
        if mode == "load-error": raise ValueError("private-path-credential-secret")
    def translate_batch(self, inputs, **kwargs):
        with marker.open("a") as out: out.write("infer\\n")
        if mode == "infer-hang": time.sleep(120)
        if mode == "infer-error": raise ValueError("private-path-credential-secret")
        prefix = kwargs["target_prefix"][0][0]
        candidate = "synthetic-" + prefix[2:-2]
        if mode == "oversized" and inputs[0][0] == "원문": candidate = "x" * 20001
        if mode == "empty-output" and inputs[0][0] == "원문": candidate = ""
        if mode == "json-output" and inputs[0][0] == "원문": candidate = "{{untrusted-output}}"
        if mode == "source-copy" and prefix == "__en__" and inputs[0][0] == "2010...":
            candidate = "2010..."
        if mode == "prefix-error": prefix = "__invalid__"
        return [types.SimpleNamespace(hypotheses=[[prefix, candidate]])]
class NativeModule(types.ModuleType):
    def __getattr__(self, name):
        if name != "M2M100Tokenizer": raise AttributeError(name)
        if mode == "descriptor-clone":
            os.close(os.dup(0))
        return Tokenizer
sys.modules["transformers"] = NativeModule("transformers")
sys.modules["ctranslate2"] = types.SimpleNamespace(Translator=Native)
from langtextflow.translation.m2m100_worker import main
main()
''', encoding="utf-8")
    arguments = [] if legacy else ["--rust-protocol"]
    return [sys.executable, str(fixture), *arguments, str(tmp_path), str(tmp_path), "2"], marker


async def child(tmp_path, mode="normal", *, legacy=False):
    command, marker = child_command(tmp_path, mode, legacy=legacy)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    return process, marker


async def send(process, value):
    process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
    await process.stdin.drain()


async def receive(process):
    return json.loads(await asyncio.wait_for(process.stdout.readline(), 3))


async def stop(process):
    if process.returncode is None:
        process.kill()
    await process.wait()


@pytest.mark.asyncio
async def test_actual_worker_reuses_native_model_all_six_directions_and_v1_identity(tmp_path):
    process, marker = await child(tmp_path)
    try:
        for source, target in [("ko", "en"), ("ko", "ja"), ("en", "ko"),
                               ("en", "ja"), ("ja", "ko"), ("ja", "en")]:
            value = request(source, target)
            await send(process, value)
            reply = await receive(process)
            assert {key: reply[key] for key in IDENTITY_KEYS} == {
                key: value[key] for key in IDENTITY_KEYS
            }
            assert reply["status"] == "ok" and reply["result"] == "synthetic-" + target
            assert reply["error"] is None
        assert marker.read_text().splitlines().count("load") == 1
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stderr.read() == b""
    finally:
        await stop(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "load-error", "infer-error", "prefix-error", "missing-cache",
])
async def test_native_failure_is_terminal_identity_bound_and_private(tmp_path, mode):
    process, marker = await child(tmp_path, mode)
    try:
        context = {"glossary": [{"term": "원문", "translations": {"en": "required"}}]}
        value = request(context=context if mode == "glossary-miss" else {})
        await send(process, value)
        reply = await receive(process)
        assert reply == {**{key: value[key] for key in IDENTITY_KEYS},
                         "status": "error", "result": None, "error": "translation_worker_failed"}
        assert await asyncio.wait_for(process.wait(), 2) == 1
        assert await process.stderr.read() == b""
        if mode == "missing-cache":
            assert not marker.exists()
    finally:
        await stop(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "source-copy", "oversized", "empty-output", "json-output", "glossary-miss",
])
async def test_completed_output_rejection_keeps_actual_rust_worker_for_peer_and_next_request(
    tmp_path, mode,
):
    process, marker = await child(tmp_path, mode)
    try:
        context = {"glossary": [{"term": "원문", "translations": {"en": "required"}}]}
        value = request(text="2010..." if mode == "source-copy" else "원문",
                        context=context if mode == "glossary-miss" else {})
        await send(process, value)
        assert await receive(process) == {
            **{key: value[key] for key in IDENTITY_KEYS}, "status": "error",
            "result": None, "error": "translation_rejected",
        }
        pid = process.pid
        for target in ("ja", "en"):
            value = request(target=target, text="healthy", context={})
            await send(process, value)
            reply = await receive(process)
            assert reply["status"] == "ok" and reply["result"] == "synthetic-" + target
            assert process.pid == pid and process.returncode is None
        assert marker.read_text().splitlines().count("load") == 1
        assert marker.read_text().splitlines().count("infer") == 3
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stderr.read() == b""
    finally:
        await stop(process)


@pytest.mark.asyncio
async def test_legacy_parent_reuses_actual_first_party_worker_after_numeric_source_copy(
    tmp_path, monkeypatch,
):
    from langtextflow.models import SessionContext
    from langtextflow.translation import m2m100_ct2 as mt
    from langtextflow.translation.base import TranslationError

    command, marker = child_command(tmp_path, "source-copy", legacy=True)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setattr(mt, "worker_command", lambda *args: command)
    provider = mt.M2M100CT2Translator(model_dir=str(tmp_path), tokenizer_dir=str(tmp_path))
    try:
        await provider.prepare()
        owner = provider._worker
        pid = owner.process.pid
        with pytest.raises(TranslationError):
            await provider.translate("2010...", source_language="ko", target_language="en",
                                     context=SessionContext())
        outputs = await asyncio.gather(*[
            provider.translate("healthy", source_language="ko", target_language=target,
                               context=SessionContext()) for target in ("ja", "en")
        ])
        assert outputs == ["synthetic-ja", "synthetic-en"]
        assert provider._worker is owner and owner.ready and owner.process.pid == pid
        assert owner.process.returncode is None
        assert marker.read_text().splitlines().count("load") == 1
        assert marker.read_text().splitlines().count("infer") == 3
    finally:
        await provider.close()
    assert owner.process.returncode is not None and owner._released


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [
    {"term": "원문", "translations": {"en": "synthetic-en"}},
    {"term": "원문", "source_language": "en", "translations": {"en": "irrelevant"}},
    {"term": "원문", "enabled": False, "translations": {"en": "irrelevant"}},
    {"term": "원문", "presets": ["church"], "translations": {"en": "irrelevant"}},
])
async def test_glossary_validation_preserves_production_source_preset_and_enabled_scope(
    tmp_path, entry,
):
    process, _ = await child(tmp_path)
    try:
        await send(process, request(context={"preset": "general", "glossary": [entry]}))
        reply = await receive(process)
        assert reply["status"] == "ok" and reply["result"] == "synthetic-en"
    finally:
        await stop(process)


@pytest.mark.parametrize("source,target", [
    ("ko", "en"), ("ko", "ja"), ("en", "ko"),
    ("en", "ja"), ("ja", "ko"), ("ja", "en"),
])
async def test_six_direction_preset_glossary_positive_and_fail_closed_native_worker(
    tmp_path, source, target,
):
    process, _ = await child(tmp_path)
    entry = {"term": "source term", "source_language": source,
             "translations": {target: "synthetic-" + target}, "presets": ["church"]}
    irrelevant = {**entry, "translations": {target: "must-not-be-required"}}
    context = {"preset": "church", "glossary": [
        entry, {**irrelevant, "source_language": "ja" if source != "ja" else "en"},
        {**irrelevant, "enabled": False}, {**irrelevant, "presets": ["conference"]},
    ]}
    try:
        await send(process, request(source, target, text="source term", context=context))
        reply = await receive(process)
        assert reply["status"] == "ok" and reply["result"] == "synthetic-" + target
        await send(process, request(source, target, text="source term", context={
            "preset": "church", "glossary": [irrelevant],
        }))
        reply = await receive(process)
        assert reply["status"] == "error" and reply["error"] == "translation_worker_failed"
        assert await asyncio.wait_for(process.wait(), 2) == 1
        assert await process.stderr.read() == b""
    finally:
        await stop(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,stage", [("load-hang", "load"), ("infer-hang", "infer")])
async def test_parent_eof_terminates_actual_worker_during_native_work(tmp_path, mode, stage):
    process, marker = await child(tmp_path, mode)
    try:
        await send(process, request())
        async with asyncio.timeout(3):
            while not marker.exists() or stage not in marker.read_text().splitlines():
                await asyncio.sleep(.005)
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stdout.read() == b"" and await process.stderr.read() == b""
    finally:
        await stop(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"x" * MAX_FRAME_BYTES, b"{}\n"],
                         ids=["unterminated-at-limit", "invalid-contract"])
async def test_unbounded_or_invalid_input_exits_before_native_load(tmp_path, content):
    process, marker = await child(tmp_path)
    try:
        process.stdin.write(content)
        await process.stdin.drain()
        assert await asyncio.wait_for(process.wait(), 2) == 1
        assert not marker.exists()
        assert await process.stdout.read() == b"" and await process.stderr.read() == b""
    finally:
        await stop(process)


@pytest.mark.asyncio
async def test_existing_python_runtime_ready_ack_and_private_protocol_are_preserved(tmp_path):
    process, _ = await child(tmp_path, legacy=True)
    try:
        assert await receive(process) == {"ready": True}
        await send(process, {"id": 1, "text": "source", "source": "en", "target": "ko"})
        assert await receive(process) == {"id": 1, "text": "synthetic-ko"}
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 1
        assert await process.stderr.read() == b""
    finally:
        await stop(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["rust-v1", "python-runtime"])
async def test_native_import_can_clone_stdin_while_the_parent_pipe_remains_open(tmp_path, legacy):
    process, _ = await child(tmp_path, "descriptor-clone", legacy=legacy)
    try:
        if legacy:
            assert await receive(process) == {"ready": True}
            await send(process, {"id": 1, "text": "source", "source": "ko", "target": "en"})
        else:
            await send(process, request())
        reply = await receive(process)
        assert reply.get("text", reply.get("result")) == "synthetic-en"
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == (1 if legacy else 0)
    finally:
        await stop(process)
