from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from pathlib import Path

import pytest

from langtextflow.inference_worker import is_prepare, read_pcm, validate_request
from langtextflow.worker_ipc import (
    INFERENCE_COMPUTE_TYPES,
    INFERENCE_DEVICES,
    validate_inference_options,
)


@pytest.mark.parametrize("device", sorted(INFERENCE_DEVICES))
@pytest.mark.parametrize("compute_type", sorted(INFERENCE_COMPUTE_TYPES))
def test_inference_options_accept_supported_values(device, compute_type):
    options = {"protocol_version": 1, "device": device, "compute_type": compute_type}
    assert validate_inference_options(options) == options


@pytest.mark.parametrize("patch", [
    {"protocol_version": True}, {"protocol_version": 2}, {"device": True},
    {"device": "gpu"}, {"compute_type": []}, {"compute_type": "invalid"},
    {"extra": 1},
])
def test_inference_options_reject_invalid_values(patch):
    options = {"protocol_version": 1, "device": "cpu", "compute_type": "int8"}
    with pytest.raises(ValueError):
        validate_inference_options(options | patch)
    with pytest.raises(ValueError):
        validate_inference_options({"device": "cpu"})


def request(path: Path) -> dict:
    return {
        "protocol_version": 1, "request_id": "r1", "session_id": "s1",
        "session_generation": 2, "segment_id": "seg", "source_revision": 1,
        "task_kind": "asr", "source_language": "ko", "target_language": None,
        "deadline_ms": 1000, "payload": json.dumps({
            "wav_path": str(path), "start_sample": 0, "end_sample": 1600,
            "sample_rate": 16_000,
        }),
    }


def wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 1600)


@pytest.mark.parametrize("patch", [
    {"session_generation": True}, {"source_revision": -1}, {"source_language": "auto"},
    {"deadline_ms": 120_001}, {"task_kind": "translation"}, {"target_language": "en"},
    {"payload": "{}"}, {"unexpected": 1},
])
def test_inference_worker_rejects_invalid_contract_before_native_load(tmp_path, patch):
    with pytest.raises(ValueError):
        validate_request({**request(tmp_path / "fixture.wav"), **patch})


def test_inference_worker_pcm_range_is_bounded_and_exact(tmp_path):
    path = tmp_path / "fixture.wav"
    wav(path)
    chunk = validate_request(request(path))
    assert read_pcm(chunk) == b"\0\0\0\0" * 1600
    with pytest.raises(ValueError, match="out_of_bounds"):
        read_pcm({**chunk, "end_sample": 1601})
    with pytest.raises(ValueError, match="invalid_audio_descriptor"):
        validate_request({**request(path), "payload": json.dumps({**chunk, "end_sample": 0})})


def test_whisper_prepare_is_distinct_from_the_legacy_wav_contract(tmp_path):
    value = request(tmp_path / "no-audio.wav")
    value["payload"] = json.dumps({"asr_protocol_version": 1, "mode": "prepare"})
    assert is_prepare(value)
    value["payload"] = json.dumps({
        "asr_protocol_version": 1, "mode": "prepare",
        "inference_options": {"protocol_version": True, "device": "cpu", "compute_type": "int8"},
    })
    with pytest.raises(ValueError):
        is_prepare(value)
    value["payload"] = json.dumps({"asr_protocol_version": 1, "mode": "prepare"})
    with pytest.raises(ValueError, match="invalid_audio_descriptor"):
        validate_request(value)  # The legacy worker schema cannot mistake this for ASR output.
    for payload in ({"asr_protocol_version": True, "mode": "prepare"},
                    {"asr_protocol_version": 1, "mode": "other"},
                    {"asr_protocol_version": 1, "mode": "prepare", "wav_path": "untrusted"}):
        value["payload"] = json.dumps(payload)
        assert not is_prepare(value)
        with pytest.raises(ValueError):
            validate_request(value)


def test_only_audio_free_prepare_has_six_hundred_second_start_budget(tmp_path):
    value = request(tmp_path / "no-audio.wav") | {"deadline_ms": 600_000}
    with pytest.raises(ValueError):
        is_prepare(value)
    with pytest.raises(ValueError):
        validate_request(value)
    value["payload"] = json.dumps({"asr_protocol_version": 1, "mode": "prepare"})
    assert is_prepare(value)
    with pytest.raises(ValueError):
        is_prepare(value | {"deadline_ms": 600_001})


async def fake_native_process(tmp_path, mode, *, reference="Reference"):
    model = tmp_path / "prepared-private-model"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (model / name).write_text("{}")
    marker = tmp_path / "native-stage"
    wrapper = tmp_path / "native_fixture.py"
    # Only external native dependencies are synthetic. The shipped worker,
    # cache resolver/loader, decoder/timestamp guard and EOF lifecycle are real.
    wrapper.write_text(f'''
import sys, types, time
from pathlib import Path
mode = {mode!r}
reference = {reference!r}
marker = Path({str(marker)!r})
class Audio:
    def astype(self, *args, **kwargs): return self
sys.modules['numpy'] = types.SimpleNamespace(
    frombuffer=lambda *args, **kwargs: Audio(), float32='float32')
class Model:
    def __init__(self, path, **kwargs):
        assert kwargs['local_files_only'] is True
        assert kwargs['device'] == ('cuda' if mode == 'options' else 'cpu')
        assert kwargs['compute_type'] == ('float16' if mode == 'options' else 'int8')
        if mode == 'options': assert not marker.exists()
        if mode == 'auto-threads':
            assert 'cpu_threads' not in kwargs and 'num_workers' not in kwargs
        else:
            assert kwargs['cpu_threads'] == 2 and kwargs['num_workers'] == 1
        marker.write_text('load')
        if mode == 'load-hang': time.sleep(120)
        if mode == 'load-error': raise ValueError('private-checkpoint-secret')
    def transcribe(self, audio, **kwargs):
        assert kwargs['vad_filter'] is True
        if mode.startswith('context-'):
            assert kwargs['language'] == mode.split('-')[1]
            prompt_prefix = 'Source title | Presenter | Description | '
            assert kwargs['initial_prompt'] == prompt_prefix + reference[:1200]
            assert kwargs['hotwords'] == 'explicit, source-term, source alias'
        else:
            assert kwargs['language'] == 'ko'
            assert kwargs['initial_prompt'] == 'ASR prototype' and kwargs['hotwords'] is None
        marker.write_text('infer')
        if mode == 'infer-hang': time.sleep(120)
        segment = types.SimpleNamespace(start=0.0, end=0.1, text='synthetic source')
        return [segment], types.SimpleNamespace(duration=0.1, duration_after_vad=0.1)
class NativeModule(types.ModuleType):
    def __getattr__(self, name):
        if name != 'WhisperModel': raise AttributeError(name)
        if mode == 'descriptor-clone':
            import os
            os.close(os.dup(0))
        return Model
sys.modules['faster_whisper'] = NativeModule('faster_whisper')
from langtextflow.inference_worker import main
main()
''')
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(wrapper), str(model), str(tmp_path),
        "auto" if mode == "auto-threads" else "2",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    return process, marker


async def test_options_prepare_binds_engine_and_rejects_changes(tmp_path):
    path = tmp_path / "fixture.wav"
    wav(path)
    process, marker = await fake_native_process(tmp_path, "options")
    options = {"protocol_version": 1, "device": "cuda", "compute_type": "float16"}
    value = request(path)
    value["payload"] = json.dumps({
        "asr_protocol_version": 1, "mode": "prepare", "inference_options": options,
    })

    async def exchange(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()
        return json.loads(await asyncio.wait_for(process.stdout.readline(), 3))

    try:
        for _ in range(2):
            reply = await exchange(value)
            assert json.loads(reply["result"]) == {
                "asr_protocol_version": 1, "prepared": True, "sample_rate": 16000,
                "inference_options": options,
            }
        assert marker.read_text() == "load"
        assert (await exchange(request(path)))["status"] == "ok"
        assert marker.read_text() == "infer"
        value["payload"] = json.dumps({
            "asr_protocol_version": 1, "mode": "prepare",
            "inference_options": options | {"device": "cpu"},
        })
        reply = await exchange(value)
        assert reply["status"] == "error" and reply["error"] == "asr_worker_failed"
        assert await asyncio.wait_for(process.wait(), 2) == 1
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("mode", ["normal", "descriptor-clone", "load-error", "auto-threads"])
async def test_actual_inference_worker_native_path_and_quiet_error(tmp_path, mode):
    path = tmp_path / "fixture.wav"
    wav(path)
    process, _ = await fake_native_process(tmp_path, mode)
    try:
        process.stdin.write((json.dumps(request(path)) + "\n").encode())
        await process.stdin.drain()
        line = await asyncio.wait_for(process.stdout.readline(), 3)
        reply = json.loads(line)
        assert reply["request_id"] == "r1" and reply["source_revision"] == 1
        assert str(tmp_path) not in line.decode()
        assert "private-checkpoint-secret" not in line.decode()
        if mode != "load-error":
            result = json.loads(reply["result"])
            assert result["sample_count"] == 1600
            assert result["segments"] == [{
                "start_seconds": 0.0, "end_seconds": 0.1, "text": "synthetic source",
                "estimated_timestamps": False,
            }]
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), 2) == 0
        else:
            assert reply["status"] == "error" and reply["error"] == "asr_worker_failed"
            assert await asyncio.wait_for(process.wait(), 2) == 1
        assert await process.stderr.read() == b""
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("mode", ["normal", "load-error", "load-hang"])
async def test_actual_whisper_prepare_loads_without_audio_or_inference_and_eof_is_owned(
    tmp_path, mode,
):
    process, marker = await fake_native_process(tmp_path, mode)
    value = request(tmp_path / "absent-audio.wav")
    value["payload"] = json.dumps({"asr_protocol_version": 1, "mode": "prepare"})
    value["deadline_ms"] = 600_000
    try:
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()
        if mode == "load-hang":
            async with asyncio.timeout(3):
                while not marker.exists():
                    await asyncio.sleep(.01)
            assert marker.read_text() == "load"
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), 3) == 0
            assert await process.stdout.read() == b""
        else:
            reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
            assert reply["request_id"] == value["request_id"]
            if mode == "normal":
                assert json.loads(reply["result"]) == {
                    "asr_protocol_version": 1, "prepared": True, "sample_rate": 16000,
                }
                assert marker.read_text() == "load"  # Native transcribe wasn't invoked.
                process.stdin.close()
                assert await asyncio.wait_for(process.wait(), 3) == 0
            else:
                assert reply["error"] == "asr_worker_failed" and reply["result"] is None
                assert await asyncio.wait_for(process.wait(), 3) == 1
        assert await process.stderr.read() == b""
        assert not (tmp_path / "absent-audio.wav").exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("source", ["ko", "en", "ja"])
@pytest.mark.parametrize("preset", ["general", "church"])
async def test_shipped_asr_worker_uses_source_scoped_context_with_native_decoder(
    tmp_path, source, preset,
):
    path = tmp_path / "fixture.wav"
    wav(path)
    reference = "r" * 3000 if (source, preset) == ("ko", "church") else "Reference"
    process, _ = await fake_native_process(tmp_path, "context-" + source, reference=reference)
    value = request(path)
    value["source_language"] = source
    chunk = json.loads(value["payload"])
    good = {"term": "source-term", "source_language": source, "aliases": ["source alias"]}
    chunk["context"] = {
        "title": "Source title", "presenter": "Presenter", "description": "Description",
        "reference_text": reference, "preset": preset, "hotwords": ["explicit"],
        "glossary": [good, {**good, "term": "wrong-source", "aliases": ["wrong alias"],
                            "source_language": "ja" if source != "ja" else "en"},
                     {**good, "term": "disabled", "enabled": False},
                     {**good, "term": "wrong-preset", "presets": ["lecture"]}],
    }
    value["payload"] = json.dumps(chunk)
    try:
        process.stdin.write((json.dumps(value) + "\n").encode())
        await process.stdin.drain()
        reply = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
        assert reply["status"] == "ok" and "source-term" not in reply["result"]
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stderr.read() == b""
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("context", [
    {"hotwords": ["x" * 161]}, {"unknown": "private"}, {"glossary": [{"term": ""}]},
    {"description": "x" * (48 * 1024)},
], ids=["hotword-limit", "unknown-field", "invalid-glossary", "transport-limit"])
def test_invalid_asr_context_is_rejected_before_model_or_pcm_read(tmp_path, context):
    value = request(tmp_path / "does-not-exist.wav")
    value["payload"] = json.dumps({**json.loads(value["payload"]), "context": context})
    with pytest.raises(ValueError):
        validate_request(value)


@pytest.mark.parametrize("mode,stage", [("load-hang", "load"), ("infer-hang", "infer")])
async def test_actual_parent_eof_terminates_blocked_native_stage(tmp_path, mode, stage):
    path = tmp_path / "fixture.wav"
    wav(path)
    process, marker = await fake_native_process(tmp_path, mode)
    try:
        process.stdin.write((json.dumps(request(path)) + "\n").encode())
        await process.stdin.drain()
        deadline = asyncio.get_running_loop().time() + 3
        while (not marker.exists() or marker.read_text() != stage
               ) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.005)
        assert marker.read_text() == stage
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        assert await process.stdout.read() == b""
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
