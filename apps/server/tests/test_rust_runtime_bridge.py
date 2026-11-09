"""Existing API and capture routes with a real Rust authority and pure fake inference."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_rust_correction_worker import ollama  # noqa: F401

from langtextflow import main
from langtextflow.config import Settings
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import StartSessionRequest, TranscriptEvent
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.runtime import CaptionRuntime
from langtextflow.rust_runtime import (
    RustRuntimeBridge,
    _python_worker,
    _runtime_binary,
    _source_context,
    validate_rust_request,
)

_ORIGIN = {"origin": "http://localhost:5173"}
_REQUEST = {
    "engine": "faster-whisper", "source_language": "en", "target_languages": ["ko", "ja"],
    "correction_provider": "none", "translation_provider": "m2m100-ct2",
}
_ASR = """
import json,sys
for line in sys.stdin:
    req=json.loads(line)
    chunk=json.loads(req['payload'])
    count=chunk['end_sample']-chunk['start_sample']
    reply={k:req[k] for k in ('protocol_version','request_id','session_id',
                             'session_generation','segment_id','source_revision')}
    result={'sample_count':count,'sample_rate':16000,'segments':[{
        'start_seconds':0.,'end_seconds':count/16000,
        'text':'Original English source','estimated_timestamps':False}]}
    reply.update(status='ok',result=json.dumps(result),error=None)
    print(json.dumps(reply),flush=True)
"""
_MT = """
import json,sys
for line in sys.stdin:
    req=json.loads(line)
    reply={k:req[k] for k in ('protocol_version','request_id','session_id',
                             'session_generation','segment_id','source_revision')}
    failed=req['target_language']=='ja'
    reply.update(status='error' if failed else 'ok',
                 result=None if failed else '한국어 번역',error='synthetic' if failed else None)
    print(json.dumps(reply,ensure_ascii=True),flush=True)
"""


@pytest.mark.parametrize("change", [
    {"engine": "auto"}, {"record_audio": True}, {"correction_provider": "unsupported"},
    {"translation_provider": "ollama"}, {"translation_model": "path/private"},
])
def test_unsupported_rust_combinations_refused_without_starting_process(tmp_path, change):
    binary = tmp_path / "runtime"
    binary.touch()
    settings = Settings(runtime_core="rust", rust_runtime_binary=str(binary))
    with pytest.raises(ValueError, match="Rust rollout requires"):
        validate_rust_request(settings, StartSessionRequest(**(_REQUEST | change)))


async def test_rust_refusal_happens_before_history_bootstrap(tmp_path):
    database = tmp_path / "must-not-create.db"
    runtime = CaptionRuntime(Settings(runtime_core="rust", database_path=str(database)))
    with pytest.raises(ValueError, match="Rust rollout requires"):
        await runtime.start(StartSessionRequest(engine="auto"))
    assert not runtime._initialized and not database.exists()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_frozen_runtime_uses_bundled_executable_and_refuses_missing_helper(
    tmp_path, monkeypatch, platform,
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    settings = Settings(runtime_core="rust")
    with pytest.raises(ValueError, match="unavailable"):
        validate_rust_request(settings, StartSessionRequest(**_REQUEST))
    binary = tmp_path / "runtime" / ("langtextflow-runtime.exe" if platform == "win32"
                                    else "langtextflow-runtime")
    binary.parent.mkdir()
    binary.write_bytes(b"trusted executable fixture")
    binary.chmod(0o700)
    validate_rust_request(settings, StartSessionRequest(**_REQUEST))
    assert _runtime_binary(settings) == binary.resolve()
    # The explicitly configured trusted helper still takes precedence.
    settings.rust_runtime_binary = str(tmp_path / "absent")
    with pytest.raises(ValueError, match="unavailable"):
        _runtime_binary(settings)


@pytest.mark.parametrize("module,flag", [
    ("langtextflow.inference_worker", "--asr-inference-worker"),
    ("langtextflow.translation.m2m100_worker", "--m2m100-worker"),
    ("langtextflow.correction_worker", "--correction-worker"),
])
def test_frozen_leaf_dispatch_is_fixed_before_windows_venv_bootstrap(monkeypatch, module, flag):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "prefix", "frozen-application")
    monkeypatch.setattr(sys, "base_prefix", "unrelated-base")
    args = ["--rust-protocol", "private cache"]
    assert _python_worker(module, args) == {"program": sys.executable, "args": [flag, *args]}
    with pytest.raises(ValueError, match="unsupported frozen"):
        _python_worker("untrusted.module", [])


def test_configured_identity_is_not_a_verified_checkpoint_or_a_local_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = RustRuntimeBridge(None, Settings(faster_whisper_model="small"), asyncio.Semaphore(1))
    request = StartSessionRequest(**_REQUEST)
    assert bridge._configured_model(request) == "small"
    (tmp_path / "small").mkdir()
    assert bridge._configured_model(request) is None
    for private in ["private/checkpoint", str(tmp_path), "https://user:secret@host/model?q=token"]:
        bridge.settings.faster_whisper_model = private
        assert bridge._configured_model(request) is None


def test_windows_venv_worker_bootstrap_owns_actual_python_pid_and_keeps_site(tmp_path, monkeypatch):
    import importlib.metadata
    import sysconfig

    from langtextflow import rust_runtime

    site_directory = sysconfig.get_path("purelib")
    module = tmp_path / "owned_runtime_test_worker.py"
    module.write_text(
        "import importlib.metadata,json,os,sys\n"
        "print(json.dumps({'pid':os.getpid(),'pytest':importlib.metadata.version('pytest'),"
        "'args':sys.argv[1:],'no_site':sys.flags.no_site}),flush=True)\n", encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv-marker"))
    monkeypatch.setattr(rust_runtime.sysconfig, "get_path", lambda name: site_directory)
    command = _python_worker("owned_runtime_test_worker", ["한글", "日本語"])
    with subprocess.Popen(
        [command["program"], *command["args"]],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as child:
        output, error = child.communicate(timeout=5)
        assert child.returncode == 0, error
        observed = json.loads(output)
        assert observed["pid"] == child.pid
        assert observed["no_site"] == 1
        assert observed["pytest"] == importlib.metadata.version("pytest")
        assert observed["args"] == ["한글", "日本語"]


@pytest.fixture
def rust_api(monkeypatch, tmp_path):
    binary = os.environ.get("LTF_RUNTIME_BINARY")
    if not binary:
        pytest.skip("actual Rust host is exercised by cargo runtime_api integration")
    settings = Settings(
        runtime_core="rust", rust_runtime_binary=binary,
        database_path=str(tmp_path / "session.db"), mdns_enabled=False,
    )
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    runtime = CaptionRuntime(settings)
    from langtextflow.translation import m2m100_ct2

    monkeypatch.setattr(m2m100_ct2, "_CHILD_SLOT", BoundedSemaphore(1))
    glossary = GlossaryRepository(settings.database_path)
    project_glossary = ProjectGlossaryRepository(settings.database_path)

    @asynccontextmanager
    async def lifespan(app):
        await runtime.initialize()
        glossary.initialize()
        project_glossary.initialize()
        try:
            yield
        finally:
            await asyncio.wait_for(runtime.shutdown(), 20)

    def commands(self, request):
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                {"program": sys.executable, "args": ["-u", "-c", _MT]})

    original_start = runtime.pipeline.start
    original_ingest = runtime.pipeline.ingest
    original_upsert = runtime.history.upsert_segment

    async def guard_start(*args, **kwargs):
        assert not runtime._rust_session, "Rust must not run Python caption inference/commit"
        return await original_start(*args, **kwargs)

    async def guard_ingest(*args, **kwargs):
        assert not runtime._rust_session, "Rust must not run Python caption inference/commit"
        return await original_ingest(*args, **kwargs)

    def no_python_event_persistence(*args, **kwargs):
        assert not runtime._rust_session, "Rust must not double-persist canonical events"
        return original_upsert(*args, **kwargs)

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    monkeypatch.setattr(runtime.pipeline, "start", guard_start)
    monkeypatch.setattr(runtime.pipeline, "ingest", guard_ingest)
    monkeypatch.setattr(runtime.history, "upsert_segment", no_python_event_persistence)
    monkeypatch.setattr(main, "runtime", runtime)
    monkeypatch.setattr(main, "glossary_repository", glossary)
    monkeypatch.setattr(main, "project_glossary_repository", project_glossary)
    monkeypatch.setattr(main, "_audio_transition_lock", asyncio.Lock())
    monkeypatch.setattr(main, "_active_audio_socket", None)
    monkeypatch.setattr(main, "_audio_socket_accepting", True)
    monkeypatch.setattr(main.app.router, "lifespan_context", lifespan)
    with TestClient(main.app, client=("127.0.0.1", 50000), headers=_ORIGIN) as client:
        yield client, runtime


def test_existing_http_capture_audience_history_and_restart_with_rust_authority(rust_api):
    client, runtime = rust_api
    old_sid = None
    for _ in range(2):
        response = client.post("/api/v1/session/start", json=_REQUEST)
        assert response.status_code == 200, response.text
        state = response.json()
        sid = state["session_id"]
        assert sid and sid != old_sid and state["running"] and state["audio_required"]
        bridge = runtime.engine
        assert isinstance(bridge, RustRuntimeBridge)
        process = bridge._process
        assert process.returncode is None
        assert runtime._persistence_task is None
        assert runtime.pipeline.queue_depth == 0
        with client.websocket_connect(f"/ws/audience/{state['join_code']}") as audience:
            assert audience.receive_json() == {"type": "snapshot", "segments": []}
            with client.websocket_connect(f"/ws/audio?expected_session_id={sid}") as audio:
                config = audio.receive_json()
                assert config == {
                    "type": "audio_config", "engine": "faster-whisper", "required": True,
                    "sample_rate": 16000, "channels": 1, "sample_format": "f32le",
                }
                # Preserve the active microphone across invalid configuration.
                invalid = client.post(
                    "/api/v1/session/start", json=_REQUEST | {"record_audio": True},
                )
                assert invalid.status_code == 400
                assert runtime.engine is bridge and runtime.state.session_id == sid
                for _frame in range(4):
                    audio.send_bytes(struct.pack("<f", .1) * 16000)
                audio.send_text("end")
                events = []
                while not events or not events[-1]["committed"]:
                    events.append(audience.receive_json())
                assert events[0]["stage"] == "stable" and not events[0]["translations"]
                final = events[-1]
                assert final["original_text"] == final["text"] == "Original English source"
                assert final["translations"] == {"ko": "한국어 번역"}
                assert final["translation_states"] == {"ko": "available", "ja": "failed"}
                assert final["asr_provenance"]["timestamp_basis"] == "sample_clock_native_hint"
                assert final["asr_provenance"]["configured_model"] == "small"
                assert final["asr_provenance"]["verified_model"] is None
                assert final["translation_provenance"]["ko"]["verified_model"] is None
                assert "token=" not in json.dumps(final) and "observer_path" not in final
            assert client.get("/api/v1/captions").json() == [
                TranscriptEvent.model_validate(final).model_dump(mode="json"),
            ]
            with client.websocket_connect("/ws/captions") as reconnect:
                snapshot = reconnect.receive_json()
                assert snapshot["segments"][0]["translations"] == {"ko": "한국어 번역"}
            assert client.post("/api/v1/session/stop", json={
                "expected_session_id": "stale-session",
            }).status_code == 409
        stopped = client.post("/api/v1/session/stop", json={"expected_session_id": sid})
        assert stopped.status_code == 200, stopped.text
        assert process.returncode == 0 and bridge._stopped
        assert runtime._rust_slot._value == 1
        detail = client.get(f"/api/v1/history/{sid}").json()
        assert detail["ended_at"] is not None
        saved = client.get(f"/api/v1/history/{sid}/captions").json()
        assert len(saved) == 1 and saved[0]["translations"] == {"ko": "한국어 번역"}
        assert saved[0]["original_text"] == final["original_text"]
        assert saved[0]["asr_provenance"] == final["asr_provenance"]
        assert "Original English source" in client.get(
            f"/api/v1/history/{sid}/export?format=srt",
        ).text
        old_sid = sid
    # Same public API with Python selected remains a usable rollback path.
    runtime.settings.runtime_core = "python"
    response = client.post("/api/v1/session/start", json={
        "engine": "mock", "translation_provider": "none",
    })
    assert response.status_code == 200 and response.json()["running"]
    assert not isinstance(runtime.engine, RustRuntimeBridge)
    assert runtime._persistence_task is not None
    assert client.post("/api/v1/session/stop").status_code == 200


def test_rust_audio_route_keeps_operator_origin_and_session_guards(rust_api):
    client, runtime = rust_api
    state = client.post("/api/v1/session/start", json=_REQUEST).json()
    for path, origin, code in [
        ("/ws/audio", "https://hostile.invalid", 4403),
        ("/ws/audio?expected_session_id=old", _ORIGIN["origin"], 4409),
    ]:
        with (pytest.raises(WebSocketDisconnect) as error,
              client.websocket_connect(path, headers={"origin": origin})):
            pass
        assert error.value.code == code
    assert runtime.state.session_id == state["session_id"] and runtime.state.running


def test_oversized_context_refused_before_transition_keeps_existing_audio(rust_api):
    client, runtime = rust_api
    state = client.post("/api/v1/session/start", json=_REQUEST).json()
    bridge = runtime.engine
    with client.websocket_connect("/ws/audio") as audio:
        assert audio.receive_json()["type"] == "audio_config"
        context = {"glossary": [{
            "term": f"term-{index}", "source_language": "en",
            "translations": {"ko": "x" * 2000},
        } for index in range(30)]}
        response = client.post("/api/v1/session/start", json=_REQUEST | {"context": context})
        assert response.status_code == 400 and "48KiB" in response.text
        assert runtime.engine is bridge and runtime.state.session_id == state["session_id"]
        audio.send_bytes(struct.pack("<f", .1) * 1600)
        audio.send_text("end")
        async def admitted():
            async with asyncio.timeout(3):
                while runtime._audio_samples_received < 1600:
                    await asyncio.sleep(.005)

        client.portal.call(admitted)
    assert client.post("/api/v1/session/stop").status_code == 200


@pytest.mark.parametrize("source,preset", [
    ("ko", "church"), ("en", "conference"), ("ja", "lecture"), ("ko", "general"),
])
def test_actual_api_captures_context_once_and_routes_source_scoped_asr_and_mt(
    rust_api, monkeypatch, tmp_path, source, preset,
):
    client, runtime = rust_api
    marker = tmp_path / "inference-context.jsonl"
    record = (f"with open({str(marker)!r},'a',encoding='utf-8') as log: "
              "log.write(json.dumps(req,ensure_ascii=True)+'\\n')\n")

    def commands(self, request):
        python = getattr(sys, "_base_executable", None) or sys.executable
        asr = _ASR.replace("    chunk=json.loads", "    " + record + "    chunk=json.loads")
        mt = _MT.replace("    reply={k:req[k]", "    " + record + "    reply={k:req[k]")
        return ({"program": python, "args": ["-u", "-c", asr]},
                {"program": python, "args": ["-u", "-c", mt]})

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    entry = {"term": "Original", "source_language": source, "aliases": ["source alias"],
             "translations": {"en": "required-en", "ko": "required-ko", "ja": "required-ja"},
             "presets": [preset]}
    request = _REQUEST | {
        "source_language": source,
        "target_languages": [language for language in ("en", "ko", "ja") if language != source],
        "context": {
            "title": "Source title", "presenter": "Presenter", "preset": preset,
            "description": "d" * 2000, "reference_text": "r" * 2000,
            "hotwords": ["explicit", "Original"],
            "glossary": [entry, {**entry, "term": "wrong-source",
                                 "source_language": "ja" if source != "ja" else "en"},
                         {**entry, "term": "disabled", "enabled": False},
                         {**entry, "term": "wrong-preset", "presets": [
                             "conference" if preset != "conference" else "lecture",
                         ]}],
        },
    }
    started = client.post("/api/v1/session/start", json=request)
    assert started.status_code == 200, started.text
    assert started.json()["context"]["description"] == "d" * 2000
    assert started.json()["context"]["reference_text"] == "r" * 2000
    expected = _source_context(StartSessionRequest(
        **(request | {"context": runtime.state.context.model_dump(mode="json")}),
    ))
    # A later edit of mutable Python metadata cannot alter Rust's captured input.
    runtime.state.context.hotwords.append("late mutation")
    runtime.state.context.glossary.clear()
    with client.websocket_connect("/ws/audio") as audio:
        assert audio.receive_json()["type"] == "audio_config"
        audio.send_bytes(struct.pack("<f", .1) * 1600)
        audio.send_text("end")
        async def admitted():
            async with asyncio.timeout(3):
                while runtime._audio_samples_received < 1600:
                    await asyncio.sleep(.005)

        client.portal.call(admitted)
    assert client.post("/api/v1/session/stop").status_code == 200
    records = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]
    asr = [value for value in records if value["task_kind"] == "asr"]
    mt = [value for value in records if value["task_kind"] == "translation"]
    assert len(asr) == 1 and len(mt) == 2
    assert json.loads(asr[0]["payload"])["context"] == expected
    assert expected["description"] == "d" * 1000 and expected["reference_text"] == "r" * 2000
    assert "late mutation" not in expected["hotwords"]
    assert all(entry["source_language"] == source for entry in expected["glossary"])
    assert "wrong-source" not in [entry["term"] for entry in expected["glossary"]]
    for value in mt:
        assert value["source_language"] == source
        assert json.loads(value["payload"])["context"] == {
            "preset": preset, "glossary": expected["glossary"],
        }
    assert runtime._rust_slot._value == 1


@pytest.mark.parametrize("provider,mode", [
    ("none", "deterministic"), ("ollama", "llm"), ("ollama", "rejected"),
    ("ollama", "unavailable"),
])
def test_actual_api_correction_preserves_original_provenance_history_and_mt_handoff(
    rust_api, monkeypatch, ollama, provider, mode,  # noqa: F811 - imported pytest fixture
):
    client, runtime = rust_api
    url, calls, response = ollama
    runtime.settings.ollama_url = url
    runtime.settings.ollama_correction_model = "fixture-model:tag"
    response["candidate"] = "요한복음 9장." if mode == "rejected" else "요한복음 3장."
    response["available"] = mode != "unavailable"
    chosen = "요한복음 3장." if mode == "llm" else "요한복음 3장"

    def commands(self, request):
        asr = _ASR.replace("Original English source", "요한 보금 3장")
        mt = _MT.replace("    failed=", (
            f"    assert json.loads(req['payload'])['text']=={chosen!r}\n    failed="
        ))
        return ({"program": sys.executable, "args": ["-u", "-c", asr]},
                {"program": sys.executable, "args": ["-u", "-c", mt]})

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "source_language": "ko", "target_languages": ["en", "ja"],
        "correction_provider": provider,
        "context": {"preset": "church", "title": "Source title", "presenter": "Presenter",
                    "reference_text": "r" * 4000},
    })
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    with (client.websocket_connect("/ws/captions") as captions,
          client.websocket_connect("/ws/audio") as audio):
        assert captions.receive_json()["type"] == "snapshot"
        assert audio.receive_json()["type"] == "audio_config"
        audio.send_bytes(struct.pack("<f", .1) * 1600)
        audio.send_text("end")
        events = [captions.receive_json()]
        while not events[-1]["committed"]:
            events.append(captions.receive_json())
    assert events[0]["stage"] == "stable" and events[0]["text"] == "요한 보금 3장"
    corrected = next(event for event in events if event["stage"] == "corrected")
    assert corrected["text"] == chosen
    assert all(event["original_text"] == "요한 보금 3장" for event in events)
    stage_order = {"stable": 1, "corrected": 2, "translated": 3, "committed": 4}
    assert [stage_order[event["stage"]] for event in events] == sorted(
        stage_order[event["stage"]] for event in events
    )
    final = events[-1]
    assert final["translation_states"] == {"en": "available", "ja": "failed"}
    provenance = final["correction"]
    assert provenance["method"] == (mode if mode in {"llm", "deterministic"} else "fallback")
    assert provenance["deterministic_changed"] and provenance["changed"]
    assert provenance["llm_attempted"] == (mode in {"llm", "rejected"})
    assert provenance["llm_applied"] == (mode == "llm")
    assert url not in json.dumps(final) and "r" * 64 not in json.dumps(final)
    if calls:
        context = json.loads(calls[0]["messages"][1]["content"])
        assert context["reference_material"] == "r" * 3000
        assert context["session_title"] == "Source title" and context["presenter"] == "Presenter"
    assert client.post("/api/v1/session/stop").status_code == 200
    saved = client.get(f"/api/v1/history/{sid}/captions").json()
    assert saved[0]["text"] == chosen and saved[0]["original_text"] == "요한 보금 3장"
    assert saved[0]["correction"] == provenance
    assert runtime._rust_slot._value == 1


def test_cancel_during_optional_prepare_does_not_start_asr_after_parent_eof(
    rust_api, monkeypatch, ollama, tmp_path,  # noqa: F811 - imported pytest fixture
):
    client, runtime = rust_api
    url, _, response = ollama
    response["prepare_delay"] = .25
    runtime.settings.ollama_url = url
    runtime.settings.ollama_correction_model = "fixture-model:tag"
    marker = tmp_path / "asr-must-not-start"

    def commands(self, request):
        return ({"program": sys.executable, "args": ["-u", "-c",
                 f"open({str(marker)!r},'w').write('started')\n" + _ASR]}, None)

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)

    async def scenario():
        task = asyncio.create_task(runtime.start(StartSessionRequest(
            **(_REQUEST | {"correction_provider": "ollama"}),
        )))
        async with asyncio.timeout(3):
            while not response["preparing"]:
                await asyncio.sleep(.005)
        bridge = runtime.engine
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
        assert not marker.exists() and runtime.engine is None and not runtime.state.running

    client.portal.call(scenario)


def test_parent_eof_preserves_terminal_raw_history_without_starting_queued_mt(
    rust_api, monkeypatch, tmp_path,
):
    from langtextflow import rust_runtime
    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_api
    correction_pid = tmp_path / "correction.pid"
    mt_started = tmp_path / "mt-started"
    worker_command = rust_runtime._python_worker
    sleeper = (
        "import json,os,sys,time\nfor line in sys.stdin:\n"
        f"    open({str(correction_pid)!r},'w').write(str(os.getpid()))\n"
        "    time.sleep(30)\n"
    )

    def worker(module, args):
        if module == "langtextflow.correction_worker":
            return {"program": getattr(sys, "_base_executable", None) or sys.executable,
                    "args": ["-u", "-c", sleeper]}
        return worker_command(module, args)

    def commands(self, request):
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                {"program": sys.executable, "args": ["-u", "-c",
                 f"open({str(mt_started)!r},'w').write('started')\n" + _MT]})

    monkeypatch.setattr(rust_runtime, "_python_worker", worker)
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    started = client.post("/api/v1/session/start", json=_REQUEST)
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    with client.websocket_connect("/ws/captions") as captions:
        assert captions.receive_json()["type"] == "snapshot"

        async def scenario():
            await runtime.feed_audio(struct.pack("<f", .1) * 64000)
            async with asyncio.timeout(3):
                while not correction_pid.exists():
                    await asyncio.sleep(.005)
            bridge = runtime.engine
            bridge._process.stdin.close()
            await asyncio.wait_for(bridge._process.wait(), 8)
            await asyncio.wait_for(bridge._reader, 2)
            with pytest.raises(AsrEngineError):
                await runtime.stop()
            assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1

        client.portal.call(scenario)
    saved = client.get(f"/api/v1/history/{sid}/captions").json()
    assert saved[0]["committed"] and saved[0]["stage"] == "committed"
    assert saved[0]["text"] == saved[0]["original_text"] == "Original English source"
    assert saved[0]["translation_states"] == {"ko": "failed", "ja": "failed"}
    assert not mt_started.exists() and not _pid_alive(int(correction_pid.read_text()))


def test_parent_eof_preserves_known_llm_attempt_and_deterministic_result_but_not_queued_attempts(
    rust_api, monkeypatch, ollama,  # noqa: F811 - imported pytest fixture
):
    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_api
    url, calls, response = ollama
    response["delay"] = 2
    runtime.settings.ollama_url = url
    runtime.settings.ollama_correction_model = "fixture-model:tag"
    runtime.settings.correction_timeout_seconds = 5
    asr = _ASR.replace("Original English source", "요한 보금 3장")
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", lambda *_: (
        {"program": sys.executable, "args": ["-u", "-c", asr]}, None,
    ))
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "source_language": "ko", "target_languages": ["en", "ja"],
        "correction_provider": "ollama", "translation_provider": "none",
        "context": {"preset": "church"},
    })
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]

    async def scenario():
        for _ in range(2):
            await runtime.feed_audio(struct.pack("<f", .1) * 64000)
        async with asyncio.timeout(3):
            while not calls or len(runtime.store.snapshot()) != 2:
                await asyncio.sleep(.005)
        bridge = runtime.engine
        bridge._process.stdin.close()
        await asyncio.wait_for(bridge._process.wait(), 8)
        await asyncio.wait_for(bridge._reader, 2)
        with pytest.raises(AsrEngineError):
            await runtime.stop()
        assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1

    client.portal.call(scenario)
    saved = client.get(f"/api/v1/history/{sid}/captions").json()
    assert len(saved) == 2 and len(calls) == 1
    active, queued = saved
    assert active["text"] == "요한복음 3장" and queued["text"] == "요한 보금 3장"
    for event, attempted in [(active, True), (queued, False)]:
        assert event["committed"] and event["stage"] == "committed"
        assert event["original_text"] == "요한 보금 3장"
        assert event["translation_states"] == {"en": "unsupported", "ja": "unsupported"}
        assert event["correction"]["method"] == "fallback"
        assert event["correction"]["llm_attempted"] is attempted
        assert event["correction"]["llm_applied"] is False
        assert event["correction"]["deterministic_changed"] is attempted
        assert event["correction"]["changed"] is attempted
        assert url not in json.dumps(event)


def test_rust_history_failure_does_not_block_source_and_later_stop_ends_session(rust_api):
    client, runtime = rust_api
    state = client.post("/api/v1/session/start", json=_REQUEST).json()
    with sqlite3.connect(runtime.settings.database_path) as database:
        database.execute("""CREATE TRIGGER reject_events BEFORE INSERT ON transcript_segments
            BEGIN SELECT RAISE(FAIL, 'private path/credential must not escape'); END""")
    with (client.websocket_connect("/ws/captions") as captions,
          client.websocket_connect("/ws/audio") as audio):
        assert captions.receive_json()["type"] == "snapshot"
        assert audio.receive_json()["type"] == "audio_config"
        audio.send_bytes(struct.pack("<f", .1) * 64000)
        audio.send_text("end")
        event = captions.receive_json()
        while not event["committed"]:
            event = captions.receive_json()
        assert event["translations"] == {"ko": "한국어 번역"}
    stopped = client.post("/api/v1/session/stop")
    assert stopped.status_code == 200 and stopped.json()["persistence_error"]
    assert "credential" not in stopped.text and "private path" not in stopped.text
    assert runtime.history.get_session(state["session_id"]).ended_at is not None
    assert runtime.history.segments(state["session_id"]) == []


def test_cancelled_start_reaps_actual_rust_host_and_releases_its_lease(rust_api, monkeypatch):
    client, runtime = rust_api
    original_reply = RustRuntimeBridge._reply

    async def scenario():
        ready = asyncio.Event()
        never = asyncio.Event()

        async def hold(self, kind, budget=15):
            reply = await original_reply(self, kind, budget)
            if kind == "ready":
                ready.set()
                await never.wait()
            return reply

        monkeypatch.setattr(RustRuntimeBridge, "_reply", hold)
        task = asyncio.create_task(runtime.start(StartSessionRequest(**_REQUEST)))
        await asyncio.wait_for(ready.wait(), 5)
        process = runtime.engine._process
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        assert process.returncode == 0 and runtime._rust_slot._value == 1
        assert not runtime.state.running and runtime.engine is None
        assert runtime.history.get_session(runtime.state.session_id).ended_at is not None

    client.portal.call(scenario)


def test_spool_startup_failure_is_bounded_redacted_and_ends_failed_metadata(
    rust_api, monkeypatch, tmp_path,
):
    from langtextflow import rust_runtime

    client, runtime = rust_api
    missing = tmp_path / "private-unavailable-spool"
    monkeypatch.setattr(rust_runtime.tempfile, "gettempdir", lambda: str(missing))
    response = client.post("/api/v1/session/start", json=_REQUEST)
    assert response.status_code == 503 and str(missing) not in response.text
    assert not runtime.state.running and runtime.engine is None
    assert runtime.history.get_session(runtime.state.session_id).ended_at is not None


def test_http_stop_cancellation_cannot_release_or_replace_an_owned_cleanup(rust_api, monkeypatch):
    client, runtime = rust_api
    assert client.post("/api/v1/session/start", json=_REQUEST).status_code == 200
    bridge = runtime.engine
    original_reply = bridge._reply

    async def scenario():
        stopped = asyncio.Event()
        release = asyncio.Event()

        async def hold(kind, budget=15):
            reply = await original_reply(kind, budget)
            if kind == "stopped":
                stopped.set()
                await release.wait()
            return reply

        monkeypatch.setattr(bridge, "_reply", hold)
        task = asyncio.create_task(runtime.stop())
        await asyncio.wait_for(stopped.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        replacement = asyncio.create_task(runtime.start(StartSessionRequest(**_REQUEST)))
        await asyncio.sleep(.05)
        assert not replacement.done() and runtime._rust_slot._value == 0
        from langtextflow.translation.m2m100_ct2 import _CHILD_SLOT

        assert not _CHILD_SLOT.acquire(blocking=False)
        transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8000", headers=_ORIGIN,
        ) as api:
            assert (await api.post("/api/v1/setup/m2m100/prepare")).status_code == 409
            assert (await api.delete("/api/v1/setup/cache/m2m100-native")).status_code == 409
        release.set()
        await asyncio.wait_for(replacement, 5)
        assert bridge._process.returncode == 0 and bridge._stopped
        assert runtime.engine is not bridge

    client.portal.call(scenario)


def _pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("termination", ["parent_eof", "oversized_frame", "nonreading_control"])
def test_parent_loss_and_invalid_framing_reap_occupied_child_and_spool(
    rust_api, monkeypatch, tmp_path, termination,
):
    from langtextflow import rust_runtime
    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_api
    marker = tmp_path / "owned-child.pid"
    native = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"

    def commands(self, request):
        # Standard-library fixture runs its actual owned interpreter on Windows,
        # without a virtualenv redirector PID between Rust and the sleeper.
        return {"program": getattr(sys, "_base_executable", None) or sys.executable,
                "args": ["-u", "-c", native, str(marker)]}, None

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    monkeypatch.setattr(rust_runtime.tempfile, "gettempdir", lambda: str(tmp_path))
    spawn = asyncio.create_subprocess_exec

    async def capture_error(*args, **kwargs):
        return await spawn(*args, **(kwargs | {"stderr": asyncio.subprocess.PIPE}))

    monkeypatch.setattr(rust_runtime.asyncio, "create_subprocess_exec", capture_error)
    assert client.post("/api/v1/session/start", json=_REQUEST).status_code == 200
    bridge = runtime.engine

    async def scenario():
        if termination != "nonreading_control":
            await runtime.feed_audio(struct.pack("<f", .1) * 64000)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        assert marker.exists(), "actual native-lane child was not started"
        pid = int(marker.read_text(encoding="ascii"))
        assert _pid_alive(pid) and list(tmp_path.glob("ltf-pcm-*"))
        if termination == "parent_eof":
            bridge._process.stdin.close()
        elif termination == "oversized_frame":
            bridge._process.stdin.write(struct.pack("<BI", 1, 1024 * 1024 + 4))
            await bridge._process.stdin.drain()
        else:
            # Do not read control ACKs. Pace commands so the bounded input
            # mailbox remains available while the actual stdout OS pipe fills.
            for _ in range(1000):
                if bridge._process.returncode is not None:
                    break
                try:
                    bridge._process.stdin.write(struct.pack("<BI", 1, 4) + bytes(4))
                    await bridge._process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    break
                await asyncio.sleep(.02)
        if termination == "nonreading_control":
            deadline = time.monotonic() + 5
            while bridge._process.returncode is None and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            observed_exit = bridge._process.returncode
            # Drain only after observing exit, so asyncio's paused stdout
            # transport cannot delay its wait() future after the OS exit.
            drain = asyncio.create_task(bridge._process.stdout.read())
            await asyncio.wait_for(bridge._process.wait(), 5)
            await asyncio.wait_for(drain, 3)
            assert observed_exit == 1
            error = await bridge._process.stderr.read()
            assert b"control_output_timeout" in error
        else:
            await asyncio.wait_for(bridge._process.wait(), 8)
            assert bridge._process.returncode == 0
        assert not _pid_alive(pid) and not list(tmp_path.glob("ltf-pcm-*"))
        # The operator sees a failed source; no implicit backend switch occurs.
        with pytest.raises(AsrEngineError):
            await runtime.stop()
        assert runtime._rust_slot._value == (0 if termination == "nonreading_control" else 1)

    client.portal.call(scenario)
