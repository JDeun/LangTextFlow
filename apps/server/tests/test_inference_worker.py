from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from pathlib import Path

import pytest

from langtextflow.inference_worker import read_pcm, validate_request


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


async def fake_native_process(tmp_path, mode):
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
marker = Path({str(marker)!r})
class Audio:
    def astype(self, *args, **kwargs): return self
sys.modules['numpy'] = types.SimpleNamespace(
    frombuffer=lambda *args, **kwargs: Audio(), float32='float32')
class Model:
    def __init__(self, path, **kwargs):
        assert kwargs['local_files_only'] is True
        assert kwargs['cpu_threads'] == 2
        marker.write_text('load')
        if mode == 'load-hang': time.sleep(120)
        if mode == 'load-error': raise ValueError('private-checkpoint-secret')
    def transcribe(self, audio, **kwargs):
        assert kwargs['vad_filter'] is True
        if mode.startswith('context-'):
            assert kwargs['language'] == mode.split('-')[1]
            assert kwargs['initial_prompt'] == 'Source title | Presenter | Description | Reference'
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
        sys.executable, str(wrapper), str(model), str(tmp_path), "2",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    return process, marker


@pytest.mark.parametrize("mode", ["normal", "descriptor-clone", "load-error"])
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


@pytest.mark.parametrize("source", ["ko", "en", "ja"])
@pytest.mark.parametrize("preset", ["general", "church"])
async def test_shipped_asr_worker_uses_source_scoped_context_with_native_decoder(
    tmp_path, source, preset,
):
    path = tmp_path / "fixture.wav"
    wav(path)
    process, _ = await fake_native_process(tmp_path, "context-" + source)
    value = request(path)
    value["source_language"] = source
    chunk = json.loads(value["payload"])
    good = {"term": "source-term", "source_language": source, "aliases": ["source alias"]}
    chunk["context"] = {
        "title": "Source title", "presenter": "Presenter", "description": "Description",
        "reference_text": "Reference", "preset": preset, "hotwords": ["explicit"],
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
