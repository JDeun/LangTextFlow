"""Existing API and capture routes with a real Rust authority and pure fake inference."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore, Event

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_rust_correction_worker import ollama  # noqa: F401
from test_translation_leaf import mt_endpoint  # noqa: F401

from langtextflow import main
from langtextflow.config import Settings
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import StartSessionRequest, TranscriptEvent
from langtextflow.post_event_api import install_post_event_routes
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.recording_api import install_recording_routes
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
    reply={k:req[k] for k in ('protocol_version','request_id','session_id',
                             'session_generation','segment_id','source_revision')}
    if chunk.get('mode')=='prepare':
        result={'asr_protocol_version':1,'prepared':True,'sample_rate':16000}
        if 'inference_options' in chunk:result['inference_options']=chunk['inference_options']
    else:
        count=chunk['end_sample']-chunk['start_sample']
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
    if json.loads(req['payload']).get('mode')=='prepare':
        reply.update(status='ok',result='ready',error=None)
        print(json.dumps(reply),flush=True)
        continue
    failed=req['target_language']=='ja'
    reply.update(status='error' if failed else 'ok',
                 result=None if failed else '한국어 번역',error='synthetic' if failed else None)
    print(json.dumps(reply,ensure_ascii=True),flush=True)
"""


@pytest.fixture
def rust_recording_api(rust_api, monkeypatch):
    client, runtime = rust_api
    # Production media routes capture their runtime/archive at installation.
    bound = FastAPI()
    install_recording_routes(bound, runtime, main._require_operator)
    shutdown = install_post_event_routes(
        bound, runtime, runtime.archive, runtime.settings, main._require_operator,
    )
    monkeypatch.setattr(main.app.router, "routes", [*bound.routes, *main.app.routes])
    try:
        yield client, runtime
    finally:
        client.portal.call(shutdown)


def assert_recordings(client, runtime, sid, expected):
    rows = runtime.archive.recordings(sid)
    assert len(rows) == len(expected)
    for row, (pcm16, offset) in zip(rows, expected, strict=True):
        response = client.get(
            f"/api/v1/history/{sid}/recordings/{row['recording_id']}/audio",
        )
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == row["sha256"]
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            assert wav.readframes(wav.getnframes()) == pcm16
        assert row["start_offset_ms"] == offset
        assert row["duration_seconds"] == len(pcm16) / 2 / 16000
        assert row["status"] == "completed" and row["finalized_at"] == row["created_at"]
    return rows


@pytest.mark.parametrize("change", [
    {"correction_provider": "unsupported"},
    {"translation_provider": "unsupported"}, {"source_language": "unknown"},
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
        await runtime.start(StartSessionRequest(correction_provider="unsupported"))
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
    ("langtextflow.asr_stream_worker", "--asr-stream-worker"),
    ("langtextflow.translation.m2m100_worker", "--m2m100-worker"),
    ("langtextflow.correction_worker", "--correction-worker"),
    ("langtextflow.translation_worker", "--translation-worker"),
])
def test_frozen_leaf_dispatch_is_fixed_before_windows_venv_bootstrap(monkeypatch, module, flag):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "prefix", "frozen-application")
    monkeypatch.setattr(sys, "base_prefix", "unrelated-base")
    args = ["--rust-protocol", "private cache"]
    if module == "langtextflow.translation_worker":
        args = []
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


@pytest.mark.parametrize("engine", ["faster-whisper", "auto"])
@pytest.mark.parametrize("server_profile,request_profile,threads", [
    ("standard", "standard", "auto"),
    ("low", "standard", "7"),
    ("standard", "low", "7"),
])
def test_worker_thread_policy(monkeypatch, engine, server_profile, request_profile, threads):
    from langtextflow import rust_runtime

    monkeypatch.setattr(rust_runtime, "resolve_whisper_model", lambda model, cache: model)
    monkeypatch.setattr(rust_runtime, "_python_worker", lambda module, args: {
        "module": module, "args": args,
    })
    settings = Settings(resource_profile=server_profile, low_resource_cpu_threads=7)
    bridge = RustRuntimeBridge(None, settings, asyncio.Semaphore(1))
    request = StartSessionRequest(
        engine=engine, resource_profile=request_profile, translation_provider="none",
    )
    asr, mt = bridge._worker_commands(request)
    assert asr["module"] == "langtextflow.inference_worker"
    assert len(asr["args"]) == 3 and asr["args"][-1] == threads and mt is None


def test_auto_bootstrap_allows_missing_fallback_cache_only_for_standard_primary(monkeypatch):
    from langtextflow import rust_runtime

    def missing(*args):
        raise FileNotFoundError("private-cache-location")

    monkeypatch.setattr(rust_runtime, "resolve_whisper_model", missing)
    settings = Settings()
    bridge = RustRuntimeBridge(None, settings, asyncio.Semaphore(1))
    request = StartSessionRequest(engine="auto", translation_provider="none")
    asr, mt = bridge._worker_commands(request)
    assert asr["args"][-1] == "auto" and mt is None
    assert bridge._auto_options(request)["whisper_ready"] is False
    settings.vibevoice_url = "malformed-unused-sidecar"
    for low in (request.model_copy(update={"resource_profile": "low"}), request):
        if low is request:
            settings.resource_profile = "low"
        assert bridge._auto_options(low)["sidecar"] is None
        with pytest.raises(FileNotFoundError):
            bridge._worker_commands(low)


def test_explicit_vibevoice_does_not_query_whisper_cache_even_in_low_profile(monkeypatch):
    from langtextflow import rust_runtime

    def forbidden(*args):
        pytest.fail("explicit VibeVoice must not query a Whisper fallback")

    monkeypatch.setattr(rust_runtime, "resolve_whisper_model", forbidden)
    for profile in ("standard", "low"):
        bridge = RustRuntimeBridge(None, Settings(resource_profile=profile), asyncio.Semaphore(1))
        request = StartSessionRequest(engine="vibevoice", translation_provider="none")
        assert bridge._worker_commands(request) == (None, None)
        options = bridge._auto_options(request)
        assert options["policy"] == "vibevoice" and options["sidecar"] is not None
        assert options["whisper_ready"] is False
        assert bridge._configured_model(request) is None


def test_mock_bootstrap_has_no_asr_worker_model_lookup_or_streaming_sidecar(monkeypatch):
    from langtextflow import rust_runtime

    def forbidden(*args):
        pytest.fail("synthetic source must not query a native model")

    monkeypatch.setattr(rust_runtime, "resolve_whisper_model", forbidden)
    bridge = RustRuntimeBridge(None, Settings(), asyncio.Semaphore(1), accepts_audio=False)
    request = StartSessionRequest(engine="mock", translation_provider="none")
    assert bridge._worker_commands(request) == (None, None)
    assert bridge._auto_options(request) is bridge._configured_model(request) is None
    assert not bridge.accepts_audio


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
        return (None if request.engine == "mock" else
                {"program": sys.executable, "args": ["-u", "-c", _ASR]},
                {"program": sys.executable, "args": ["-u", "-c", _MT]}
                if request.translation_provider != "none" else None)

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


@pytest.mark.parametrize("provider,unavailable", [
    ("demo", False), ("ollama", False), ("openai-compatible", False),
    ("ollama", True), ("openai-compatible", True),
])
def test_actual_general_mt_leaves_ready_state_context_and_failed_target_provenance(
    rust_api, monkeypatch, mt_endpoint, provider, unavailable,  # noqa: F811
):
    client, runtime = rust_api
    endpoint, calls, state = mt_endpoint
    state["unavailable"] = unavailable
    runtime.settings.ollama_url = endpoint
    runtime.settings.openai_compatible_url = endpoint
    runtime.settings.openai_compatible_api_key = "owned-synthetic-key"

    def commands(self, request):
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                _python_worker("langtextflow.translation_worker", []))

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "translation_provider": provider, "translation_model": "configured:tag",
        "source_language": "es", "target_languages": ["fr", "en"],
        "context": {"title": "MT title", "presenter": "Presenter", "reference_text": "r" * 5000,
                    "glossary": [{"term": "Original", "source_language": "es",
                                  "translations": {"fr": "Terme", "en": "Term"}}]},
    })
    assert started.status_code == 200, started.text
    ready = started.json()["translation_status"]
    assert ready["enabled"] and ready["provider"] == provider
    assert ready["available"] is (not unavailable)
    assert ready["model"] == ("deterministic-demo" if provider == "demo" else "configured:tag")
    sid = started.json()["session_id"]
    with (client.websocket_connect("/ws/captions") as captions,
          client.websocket_connect("/ws/audio") as audio):
        assert captions.receive_json()["type"] == "snapshot"
        assert audio.receive_json()["type"] == "audio_config"
        audio.send_bytes(struct.pack("<f", .1) * 1600)
        audio.send_text("end")
        event = captions.receive_json()
        while not event["committed"]:
            event = captions.receive_json()
        assert event["original_text"] == "Original English source"
        if unavailable:
            assert set(event["translation_states"].values()) == {"failed"} and not calls
        elif provider == "demo":
            assert event["translations"]["fr"] == "[FR demo] Original English source"
        else:
            assert event["translation_states"] == {"fr": "failed", "en": "available"}
            assert len(calls) == 2
            prompt = json.loads(calls[0][0]["messages"][1]["content"])
            assert prompt["reference_material"] == "r" * 5000
            assert prompt["terminology_mappings"] == [{"source": "Original", "target": "Terme"}]
            assert calls[0][1] == ("Bearer owned-synthetic-key" if provider == "openai-compatible"
                                   else None)
        assert set(event["translation_provenance"]) == {"fr", "en"}
        assert all(value["provider"] == provider and value["verified_model"] is None
                   for value in event["translation_provenance"].values())
        assert endpoint not in json.dumps(event) and "owned-synthetic-key" not in json.dumps(event)
    assert client.post("/api/v1/session/stop").status_code == 200
    stored = runtime.history.segments(sid)[0].model_dump(mode="json")
    assert stored["translation_provenance"] == event["translation_provenance"]
    assert stored["translations"] == event["translations"]
    final_status = client.get("/api/v1/state").json()["translation_status"]
    assert final_status["available"] is (not unavailable)


def test_actual_demo_all_ui_languages_are_admitted_without_m2m_pair_or_four_job_limit(
    rust_api, monkeypatch,
):
    from langtextflow.worker_ipc import SUPPORTED_LANGUAGES

    client, runtime = rust_api

    def commands(self, request):
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                _python_worker("langtextflow.translation_worker", []))

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    # Ten UI languages include the source, so all nine distinct targets must fit.
    targets = sorted(SUPPORTED_LANGUAGES - {"fr"})
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "translation_provider": "demo", "source_language": "fr", "target_languages": targets,
    })
    assert started.status_code == 200, started.text
    assert started.json()["translation_status"]["available"]
    with (client.websocket_connect("/ws/captions") as captions,
          client.websocket_connect("/ws/audio") as audio):
        captions.receive_json()
        audio.receive_json()
        audio.send_bytes(struct.pack("<f", .1) * 1600)
        audio.send_text("end")
        event = captions.receive_json()
        while not event["committed"]:
            event = captions.receive_json()
        assert event["translation_states"] == dict.fromkeys(targets, "available")
        assert set(event["translations"]) == set(event["translation_provenance"]) == set(targets)
    assert client.post("/api/v1/session/stop").status_code == 200


def test_actual_mt_prepare_timeout_reaps_optional_provider_and_preserves_source(
    rust_api, monkeypatch, mt_endpoint,  # noqa: F811
):
    client, runtime = rust_api
    endpoint, calls, state = mt_endpoint
    state.update(unavailable=False, prepare_delay=3.)
    runtime.settings.ollama_url = endpoint
    runtime.settings.provider_prepare_timeout_seconds = 2.

    def commands(self, request):
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                _python_worker("langtextflow.translation_worker", []))

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "translation_provider": "ollama", "translation_model": "configured:tag",
    })
    assert started.status_code == 200, started.text
    assert not started.json()["translation_status"]["available"]
    with (client.websocket_connect("/ws/captions") as captions,
          client.websocket_connect("/ws/audio") as audio):
        captions.receive_json()
        audio.receive_json()
        audio.send_bytes(struct.pack("<f", .1) * 1600)
        audio.send_text("end")
        event = captions.receive_json()
        while not event["committed"]:
            event = captions.receive_json()
        assert event["original_text"] == "Original English source"
        assert set(event["translation_states"].values()) == {"failed"}
    assert not calls and state["gets"] == 1
    assert client.post("/api/v1/session/stop").status_code == 200
    assert runtime._rust_slot._value == 1


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
                    "/api/v1/session/start", json=_REQUEST | {"correction_provider": "unsupported"},
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


@pytest.mark.parametrize("engine,profile", [("auto", "standard"), ("vibevoice", "low")])
def test_actual_primary_48k_uses_existing_audio_audience_history_and_restart(
    rust_api, engine, profile,
):
    from test_asr_stream_worker import sidecar

    client, runtime = rust_api
    request = _REQUEST | {
        "engine": engine, "resource_profile": profile, "translation_provider": "none",
        "context": {
            "title": "AUTO title", "presenter": "Speaker", "description": "Description",
            "reference_text": "Reference", "hotwords": ["explicit"], "preset": "general",
            "glossary": [
                {"term": "Original", "aliases": ["alias"], "source_language": "en",
                 "translations": {"ko": "원문"}},
                {"term": "wrong source", "source_language": "ko",
                 "translations": {"en": "wrong"}},
            ],
        },
    }
    for _ in range(2):
        with client.portal.wrap_async_context_manager(sidecar(
            rate=48000, text="Original English source",
        )) as (endpoint, observed):
            runtime.settings.vibevoice_url = endpoint
            runtime.settings.audio_queue_chunks = 3
            started = client.post("/api/v1/session/start", json=request)
            assert started.status_code == 200, started.text
            state = started.json()
            sid, bridge = state["session_id"], runtime.engine
            assert state["engine"] == "vibevoice" and state["audio_sample_rate"] == 48000
            runtime.state.context.title = "later mutable metadata"
            assert bridge.sample_rate == 48000 and bridge.queue_capacity == 3
            with client.websocket_connect(f"/ws/audience/{state['join_code']}") as audience:
                assert audience.receive_json()["segments"] == []
                with client.websocket_connect(f"/ws/audio?expected_session_id={sid}") as audio:
                    assert audio.receive_json()["sample_rate"] == 48000
                    pcm = struct.pack("<f", .25) * 48000
                    audio.send_bytes(pcm)
                    first = audience.receive_json()
                    assert first["original_text"] == "Original English source"
                    assert first["asr_provenance"]["provider"] == "vibevoice"
                    assert first["asr_provenance"]["timestamp_basis"] == "sequence_estimated"
                    assert first["asr_provenance"]["sample_rate"] == 48000
                    assert first["asr_provenance"]["audio_chunk_end_sample"] == 48000
                    assert first["asr_provenance"]["configured_model"] is None
                    audio.send_text("end")
                stopped = client.post("/api/v1/session/stop")
                assert stopped.status_code == 200, stopped.text
            assert observed["connections"] == 1 and bytes(observed["pcm"]) == pcm
            assert observed["context"]["context_info"] == (
                "AUTO title | Speaker | Description | Reference | explicit, Original, alias"
            )
            assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
            detail = client.get(f"/api/v1/history/{sid}").json()
            assert detail["engine"] == "vibevoice" and detail["ended_at"] is not None
            saved = client.get(f"/api/v1/history/{sid}/captions").json()
            assert saved and all(row["end_ms"] <= 1000 for row in saved)
            assert all(row["asr_provenance"]["auto_generation"] == (
                0 if engine == "auto" else None
            ) for row in saved)
            if engine == "vibevoice":
                assert all(row["asr_provenance"]["timeline_offset_samples"] is None
                           and row["asr_provenance"]["replay_prefix_samples"] is None
                           for row in saved)


def test_actual_mock_and_general_demo_leaf_keep_synthetic_scope_status_and_target_identity(
    rust_api, monkeypatch,
):
    client, runtime = rust_api
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", lambda self, request: (
        None, _python_worker("langtextflow.translation_worker", []),
    ))
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "engine": "mock", "translation_provider": "demo", "target_languages": ["ko", "fr"],
    })
    assert started.status_code == 200, started.text
    state, bridge = started.json(), runtime.engine
    assert not state["audio_required"] and not bridge.accepts_audio
    assert state["translation_status"]["available"]
    assert state["translation_status"]["model"] == "deterministic-demo"
    with client.websocket_connect("/ws/captions") as captions:
        snapshot = captions.receive_json()["segments"]
        event = snapshot[-1] if snapshot else captions.receive_json()
        while not event["committed"]:
            event = captions.receive_json()
        assert event["translation_states"] == {"ko": "available", "fr": "available"}
        assert event["asr_provenance"]["provider"] == "mock"
        assert event["asr_provenance"]["timestamp_basis"] == "synthetic"
        assert event["asr_provenance"]["sample_rate"] is None
        assert all(value["provider"] == "demo" for value in event["translation_provenance"].values())
        assert event["translations"]["fr"].startswith("[FR demo]")
    assert client.post("/api/v1/session/stop").status_code == 200
    assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
    saved = runtime.history.segments(state["session_id"])[0]
    assert saved.translation_provenance["fr"].provider == "demo"
    metrics = client.get("/api/v1/metrics").json()["rust_authority"]
    assert metrics["sample"]["ingress"] is None


def test_rust_mock_preserves_demo_cadence_targets_history_and_restart_without_pcm(
    rust_recording_api,
):
    from langtextflow.asr.mock import SAMPLES

    client, runtime = rust_recording_api
    old_sid = None
    for cycle in range(2):
        started = client.post("/api/v1/session/start", json=_REQUEST | {"engine": "mock"})
        assert started.status_code == 200, started.text
        state, bridge = started.json(), runtime.engine
        sid = state["session_id"]
        assert sid != old_sid and state["running"] and not state["audio_required"]
        assert state["engine"] == "mock" and not bridge.accepts_audio
        assert not client.get("/api/v1/session/recording").json()["available"]
        refused = client.post("/api/v1/session/start", json=_REQUEST | {
            "engine": "mock", "record_audio": True,
        })
        assert refused.status_code == 400 and runtime.engine is bridge
        with pytest.raises(RuntimeError, match="no active audio ASR session"):
            client.portal.call(runtime.feed_audio, struct.pack("<f", .1) * 160)
        client.portal.call(runtime.end_audio)
        assert bridge.running  # End-of-microphone is a no-op for the timer source.
        with client.websocket_connect(f"/ws/audience/{state['join_code']}") as audience:
            snapshot = audience.receive_json()["segments"]
            partial = snapshot[0] if snapshot else audience.receive_json()
            assert partial["stage"] == "partial" and partial["version"] == 1
            assert partial["segment_id"] == "demo-000-00" and partial["text"] == SAMPLES[0][:-3]
            assert partial["confidence"] == .72
            assert partial["original_text"] is partial["end_ms"] is None
            assert partial["translation_states"] == partial["translations"] == {}
            events = []
            while not events or not events[-1]["committed"]:
                events.append(audience.receive_json())
            stable, final = events[0], events[-1]
            assert stable["stage"] == "stable" and stable["version"] == 2
            assert stable["text"] == stable["original_text"] == SAMPLES[0]
            assert (stable["start_ms"], stable["end_ms"], stable["confidence"]) == (0, 5000, .93)
            assert final["translation_states"] == {"ko": "available", "ja": "failed"}
            if cycle == 0:
                next_partial = audience.receive_json()
                assert next_partial["segment_id"] == "demo-000-01"
                assert next_partial["stage"] == "partial" and next_partial["start_ms"] == 6000
                assert next_partial["text"] == SAMPLES[1][:-3]
                while True:
                    third = audience.receive_json()
                    if third["segment_id"] == "demo-000-02":
                        break
                assert third["stage"] == "partial" and third["start_ms"] == 12000
                assert third["text"] == SAMPLES[2][:-3]
        assert client.post("/api/v1/session/stop").status_code == 200
        assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
        metrics = client.get("/api/v1/metrics").json()
        assert metrics["rust_authority"]["status"] == "final"
        sample = metrics["rust_authority"]["sample"]
        assert sample["ingress"] is None
        assert metrics["asr_queue_depth"] is metrics["asr_queue_capacity"] is None
        assert sample["source"]["active_provider"] == "mock"
        saved = client.get(f"/api/v1/history/{sid}/captions").json()
        assert saved[0]["text"] == SAMPLES[0] and saved[0]["committed"]
        for row in saved:
            origin = row["asr_provenance"]
            assert origin["provider"] == "mock" and origin["timestamp_basis"] == "synthetic"
            assert all(origin[key] is None for key in (
                "sample_rate", "audio_chunk_start_sample", "audio_chunk_end_sample",
                "configured_model", "verified_model", "auto_generation", "timeline_offset_samples",
                "replay_prefix_samples",
            ))
        assert client.get(f"/api/v1/history/{sid}").json()["ended_at"] is not None
        assert runtime.archive.recordings(sid) == []
        old_sid = sid


def test_rust_mock_stop_during_partial_does_not_invent_stable_or_committed_source(rust_api):
    client, runtime = rust_api
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "engine": "mock", "translation_provider": "none",
    })
    assert started.status_code == 200, started.text
    state, bridge = started.json(), runtime.engine
    with client.websocket_connect(f"/ws/audience/{state['join_code']}") as audience:
        snapshot = audience.receive_json()["segments"]
        partial = snapshot[0] if snapshot else audience.receive_json()
        assert partial["stage"] == "partial"
        assert client.post("/api/v1/session/stop").status_code == 200
    saved = client.get(f"/api/v1/history/{state['session_id']}/captions").json()
    assert len(saved) == 1 and saved[0]["stage"] == "partial" and not saved[0]["committed"]
    assert saved[0]["version"] == 1 and saved[0]["original_text"] is saved[0]["end_ms"] is None
    assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1


def test_configured_whisper_window_vad_view_and_requested_device_reach_actual_host(
    rust_api, monkeypatch, tmp_path,
):
    from langtextflow.telemetry import EnergyVad

    client, runtime = rust_api
    runtime.settings.faster_whisper_chunk_seconds = 2.5
    runtime.settings.faster_whisper_device = "cuda"
    runtime.settings.faster_whisper_compute_type = "float16"
    runtime.settings.vad_threshold_dbfs = -15.
    runtime.settings.vad_hangover_frames = 260
    runtime.settings.max_segments = runtime.store.max_segments = 1
    runtime._vad = EnergyVad(threshold_dbfs=-15., hangover_frames=260)
    marker = tmp_path / "private-prepare-requests.jsonl"
    original = RustRuntimeBridge._worker_commands

    def commands(self, request):
        asr, _ = original(self, request)
        tracked = _ASR.replace("    req=json.loads(line)",
            f"    req=json.loads(line)\n    open({str(marker)!r},'a').write(line)")
        return asr | {"args": ["-u", "-c", tracked]}, None

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    started = client.post("/api/v1/session/start", json=_REQUEST | {"translation_provider": "none"})
    assert started.status_code == 200, started.text
    bridge, sid = runtime.engine, started.json()["session_id"]
    with client.websocket_connect(f"/ws/audio?expected_session_id={sid}") as audio:
        assert audio.receive_json()["sample_rate"] == 16000
        audio.send_bytes(struct.pack("<f", .1) * 70000)
        audio.send_text("end")
        async def accepted():
            async with asyncio.timeout(3):
                while runtime._audio_samples_received < 70000:
                    await asyncio.sleep(.005)
        client.portal.call(accepted)
    assert client.post("/api/v1/session/stop").status_code == 200
    records = [json.loads(line) for line in marker.read_text().splitlines()]
    prepare, *chunks = [json.loads(row["payload"]) for row in records]
    assert prepare["inference_options"] == {
        "protocol_version": 1, "device": "cuda", "compute_type": "float16",
    }  # Fake leaf ACK, not a GPU load or hardware qualification.
    assert [(row["start_sample"], row["end_sample"]) for row in chunks] == [
        (0, 40000), (0, 30000),  # Each private WAV begins at file offset zero.
    ]
    saved = client.get(f"/api/v1/history/{sid}/captions").json()
    assert len(saved) == 2 and all(row["committed"] for row in saved)
    assert [(row["start_ms"], row["end_ms"]) for row in saved] == [(0, 2500), (2500, 4375)]
    assert [(row["asr_provenance"]["audio_chunk_start_sample"],
             row["asr_provenance"]["audio_chunk_end_sample"]) for row in saved] == [
        (0, 40000), (40000, 70000),  # Public authority retains the global sample clock.
    ]
    assert len(client.get("/api/v1/captions").json()) == 1
    sample = bridge.metrics_view().sample
    assert sample.ingress.accepted_samples == sample.ingress.processed_samples == 70000
    assert sample.ingress.decoded_windows == 2 and not sample.ingress.voice_active


def test_large_single_vibevoice_frame_uses_bounded_leaf_batches_and_negotiated_clock(
    rust_api, monkeypatch,
):
    from test_asr_stream_worker import sidecar

    client, runtime = rust_api
    runtime.settings.max_audio_frame_bytes = runtime._vad.max_frame_bytes = 4 * 1024 * 1024
    runtime.settings.faster_whisper_chunk_seconds = 120  # Does not configure VibeVoice windows.
    monkeypatch.setattr(main, "settings", runtime.settings)
    with client.portal.wrap_async_context_manager(sidecar(rate=48000)) as (url, seen):
        runtime.settings.vibevoice_url = url
        started = client.post("/api/v1/session/start", json=_REQUEST | {
            "engine": "vibevoice", "translation_provider": "none",
        })
        assert started.status_code == 200, started.text
        bridge, sid = runtime.engine, started.json()["session_id"]
        pcm = struct.pack("<f", .1) * (262144 + 16000)
        with client.websocket_connect(f"/ws/audio?expected_session_id={sid}") as audio:
            assert audio.receive_json()["sample_rate"] == 48000
            audio.send_bytes(pcm)
            audio.send_text("end")
            async def accepted():
                async with asyncio.timeout(3):
                    while runtime._audio_samples_received < len(pcm) // 4:
                        await asyncio.sleep(.005)
            client.portal.call(accepted)
        assert client.post("/api/v1/session/stop").status_code == 200
        assert bytes(seen["pcm"]) == pcm and seen["connections"] == 1
        saved = client.get(f"/api/v1/history/{sid}/captions").json()
        assert saved and saved[0]["end_ms"] == 2000
        assert all(row["end_ms"] <= round(len(pcm) // 4 * 1000 / 48000) for row in saved)
        assert all(row["asr_provenance"]["sample_rate"] == 48000 for row in saved)
        metrics = bridge.metrics_view().sample.ingress
        assert metrics.accepted_frames == 1 and metrics.accepted_samples == len(pcm) // 4
        assert metrics.processed_samples == metrics.accepted_samples


def test_invalid_audio_settings_never_replace_a_running_session_or_echo_private_values(rust_api):
    client, runtime = rust_api
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "engine": "mock", "translation_provider": "none",
    })
    assert started.status_code == 200
    bridge, sid = runtime.engine, started.json()["session_id"]
    runtime.settings.faster_whisper_device = "private/credential-device"
    refused = client.post("/api/v1/session/start", json=_REQUEST)
    assert refused.status_code == 400 and "credential-device" not in refused.text
    assert runtime.engine is bridge and runtime.state.session_id == sid
    assert bridge._process.returncode is None
    runtime.settings.faster_whisper_device = "auto"
    assert client.post("/api/v1/session/stop").status_code == 200


def test_actual_auto_disconnect_replays_without_python_pipeline_or_metadata_writes(
    rust_api, monkeypatch,
):
    from test_asr_stream_worker import sidecar

    client, runtime = rust_api
    metadata_writes = []

    def no_python_engine_update(*args):
        metadata_writes.append(args)
        raise AssertionError("Rust owns canonical provider metadata")

    monkeypatch.setattr(runtime.history, "update_engine", no_python_engine_update)
    with client.portal.wrap_async_context_manager(sidecar(
        rate=16000, fault="disconnect-second", text="Original English source",
    )) as (endpoint, observed):
        runtime.settings.vibevoice_url = endpoint
        runtime.settings.asr_health_check_seconds = .01
        started = client.post("/api/v1/session/start", json=_REQUEST | {"engine": "auto"})
        assert started.status_code == 200, started.text
        state = started.json()
        sid, bridge = state["session_id"], runtime.engine
        with client.websocket_connect(f"/ws/audience/{state['join_code']}") as audience:
            assert audience.receive_json()["segments"] == []
            with client.websocket_connect(f"/ws/audio?expected_session_id={sid}") as audio:
                assert audio.receive_json()["sample_rate"] == 16000
                pcm = struct.pack("<f", .1) * 64000
                audio.send_bytes(pcm)
                first = audience.receive_json()
                while not first["committed"]:
                    first = audience.receive_json()
                assert first["asr_provenance"]["provider"] == "vibevoice"
                audio.send_bytes(pcm)
                replayed = audience.receive_json()
                while (not replayed["committed"]
                       or replayed["asr_provenance"]["provider"] != "faster-whisper"):
                    replayed = audience.receive_json()
                assert replayed["start_ms"] == 4000 and replayed["end_ms"] == 8000
                assert replayed["original_text"] == "Original English source"
                assert replayed["translation_states"] == {"ko": "available", "ja": "failed"}
                assert replayed["asr_provenance"]["auto_generation"] == 1
                assert replayed["asr_provenance"]["timeline_offset_samples"] == 0
                assert replayed["asr_provenance"]["replay_prefix_samples"] == 128000
                assert replayed["asr_provenance"]["configured_model"] == "small"
                audio.send_text("end")
            stopped = client.post("/api/v1/session/stop")
            assert stopped.status_code == 200, stopped.text
        assert observed["connections"] == 1
        assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
        detail = client.get(f"/api/v1/history/{sid}").json()
        assert detail["engine"] == "faster-whisper" and detail["ended_at"] is not None
        saved = client.get(f"/api/v1/history/{sid}/captions").json()
        assert len(saved) == 2 and saved[0]["original_text"] == first["original_text"]
        assert saved[0]["asr_provenance"]["provider"] == "vibevoice"
        assert saved[1]["asr_provenance"] == replayed["asr_provenance"]
        assert metadata_writes == []


@pytest.mark.parametrize("where", ["request", "server"])
def test_actual_auto_low_resource_never_queries_or_connects_sidecar(rust_api, where):
    from test_asr_stream_worker import sidecar

    client, runtime = rust_api
    request = _REQUEST | {"engine": "auto", "translation_provider": "none"}
    if where == "request":
        request["resource_profile"] = "low"
    else:
        runtime.settings.resource_profile = "low"
    runtime.settings.audio_queue_chunks = 3
    with client.portal.wrap_async_context_manager(sidecar(rate=48000)) as (endpoint, observed):
        runtime.settings.vibevoice_url = endpoint
        started = client.post("/api/v1/session/start", json=request)
        assert started.status_code == 200, started.text
        bridge, sid = runtime.engine, runtime.state.session_id
        assert bridge._auto_options(StartSessionRequest(**request))["sidecar"] is None
        assert started.json()["engine"] == "faster-whisper"
        assert bridge.sample_rate == 16000 and bridge.queue_capacity == 3
        client.portal.call(runtime.feed_audio, struct.pack("<f", .1) * 1600)
        client.portal.call(runtime.end_audio)
        assert client.post("/api/v1/session/stop").status_code == 200
        assert observed["connections"] == 0 and not observed["config_requested"].is_set()
        saved = client.get(f"/api/v1/history/{sid}/captions").json()
        assert len(saved) == 1 and saved[0]["asr_provenance"]["auto_generation"] == 0
        assert saved[0]["asr_provenance"]["provider"] == "faster-whisper"
        assert saved[0]["asr_provenance"]["configured_model"] == "base"


def test_actual_auto_startup_refusal_prepares_cached_fallback_without_live_failover_count(rust_api):
    from test_asr_stream_worker import sidecar

    client, runtime = rust_api
    with client.portal.wrap_async_context_manager(sidecar(
        rate=48000, fault="config-redirect",
    )) as (endpoint, observed):
        runtime.settings.vibevoice_url = endpoint
        started = client.post("/api/v1/session/start", json=_REQUEST | {
            "engine": "auto", "translation_provider": "none",
        })
        assert started.status_code == 200, started.text
        bridge, sid = runtime.engine, runtime.state.session_id
        assert started.json()["engine"] == "faster-whisper" and bridge.sample_rate == 16000
        client.portal.call(runtime.feed_audio, struct.pack("<f", .1) * 1600)
        client.portal.call(runtime.end_audio)
        assert client.post("/api/v1/session/stop").status_code == 200
        assert observed["connections"] == 0 and observed["config_requested"].is_set()
        assert bridge._metrics.source.auto_generation == 0
        assert bridge._metrics.source.failover_count == 0
        saved = client.get(f"/api/v1/history/{sid}/captions").json()
        assert saved[0]["asr_provenance"]["auto_generation"] == 0
        assert saved[0]["asr_provenance"]["provider"] == "faster-whisper"


@pytest.mark.parametrize("fault", ["config-redirect", "disconnect"])
def test_explicit_vibevoice_failure_never_starts_fallback_and_releases_owned_child(
    rust_api, monkeypatch, tmp_path, fault,
):
    from test_asr_stream_worker import sidecar

    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_api
    marker = tmp_path / "forbidden-whisper"
    commands = RustRuntimeBridge._worker_commands

    def tracked(self, request):
        asr, mt = commands(self, request)
        return asr | {"args": ["-u", "-c", f"open({str(marker)!r},'w').close()\n" + _ASR]}, mt

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", tracked)
    with client.portal.wrap_async_context_manager(sidecar(rate=16000, fault=fault)) as (url, _):
        runtime.settings.vibevoice_url = url
        response = client.post("/api/v1/session/start", json=_REQUEST | {
            "engine": "vibevoice", "translation_provider": "none",
        })
        if fault == "config-redirect":
            assert response.status_code == 503
        else:
            assert response.status_code == 200, response.text
            bridge = runtime.engine
            client.portal.call(runtime.feed_audio, struct.pack("<f", .1) * 1600)
            # The sidecar may close before or after admission; stopping observes
            # the terminal source error and still confirms child/spool cleanup.
            with pytest.raises(AsrEngineError):
                client.post("/api/v1/session/stop")
            assert bridge._process.returncode == 0
        assert not marker.exists() and runtime._rust_slot._value == 1
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    assert client.post("/api/v1/session/start", json=_REQUEST).status_code == 200
    assert client.post("/api/v1/session/stop").status_code == 200


def test_confirmed_failed_prepare_releases_native_lease_for_a_new_session(rust_api, monkeypatch):
    client, runtime = rust_api
    command = RustRuntimeBridge._worker_commands

    def invalid_prepare(self, request):
        asr, mt = command(self, request)
        return asr | {"args": ["-u", "-c", _ASR.replace("'prepared':True", "'prepared':False")]}, mt

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", invalid_prepare)
    failed = client.post("/api/v1/session/start", json=_REQUEST)
    assert failed.status_code == 503
    assert runtime._rust_slot._value == 1  # Model refusal is distinct from unconfirmed reap.
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", command)
    started = client.post("/api/v1/session/start", json=_REQUEST)
    assert started.status_code == 200, started.text
    assert client.post("/api/v1/session/stop").status_code == 200


def test_actual_auto_rate_mismatch_never_spawns_whisper_and_confirmed_stop_allows_retry(
    rust_api, monkeypatch, tmp_path,
):
    from test_asr_stream_worker import sidecar

    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_api
    marker = tmp_path / "whisper-must-not-start"
    command = RustRuntimeBridge._worker_commands

    def tracked(self, request):
        asr, mt = command(self, request)
        return asr | {"args": ["-u", "-c", (
            f"open({str(marker)!r},'w').write('started')\n" + _ASR
        )]}, mt

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", tracked)
    with client.portal.wrap_async_context_manager(sidecar(
        rate=48000, fault="disconnect",
    )) as (endpoint, _):
        runtime.settings.vibevoice_url = endpoint
        started = client.post("/api/v1/session/start", json=_REQUEST | {
            "engine": "auto", "translation_provider": "none",
        })
        assert started.status_code == 200, started.text
        bridge = runtime.engine
        client.portal.call(runtime.feed_audio, struct.pack("<f", .1) * 192000)
        with pytest.raises(AsrEngineError):
            client.portal.call(runtime.end_audio)
        assert not marker.exists(), "48kHz capture must not be passed to the 16kHz fallback"
        with pytest.raises(AsrEngineError):
            client.post("/api/v1/session/stop")
        assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
        assert runtime.history.get_session(started.json()["session_id"]).ended_at is not None
        monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", command)
        retry = client.post("/api/v1/session/start", json=_REQUEST | {
            "engine": "auto", "resource_profile": "low", "translation_provider": "none",
        })
        assert retry.status_code == 200, retry.text
        assert client.post("/api/v1/session/stop").status_code == 200


def test_cancelled_auto_config_prepare_reaps_leaf_without_starting_fallback_and_can_restart(
    rust_api, monkeypatch, tmp_path,
):
    from test_asr_stream_worker import sidecar

    client, runtime = rust_api
    marker = tmp_path / "fallback-must-not-start"
    command = RustRuntimeBridge._worker_commands

    def tracked(self, request):
        asr, mt = command(self, request)
        return asr | {"args": ["-u", "-c", (
            f"open({str(marker)!r},'w').write('started')\n" + _ASR
        )]}, mt

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", tracked)
    with client.portal.wrap_async_context_manager(sidecar(fault="config-gate")) as (
        endpoint, observed,
    ):
        runtime.settings.vibevoice_url = endpoint

        async def scenario():
            task = asyncio.create_task(runtime.start(StartSessionRequest(**(_REQUEST | {
                "engine": "auto", "translation_provider": "none",
            }))))
            await asyncio.wait_for(observed["config_requested"].wait(), 3)
            bridge = runtime.engine
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
            assert runtime.engine is None and not runtime.state.running
            assert not marker.exists() and observed["connections"] == 0

        try:
            client.portal.call(scenario)
        finally:
            client.portal.call(observed["release_config"].set)
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", command)
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "engine": "auto", "resource_profile": "low", "translation_provider": "none",
    })
    assert started.status_code == 200, started.text
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
    asr_requests = [value for value in records if value["task_kind"] == "asr"]
    assert json.loads(asr_requests[0]["payload"]) == {
        "asr_protocol_version": 1, "mode": "prepare",
        "inference_options": {"protocol_version": 1, "device": "auto", "compute_type": "default"},
    }
    asr = [value for value in asr_requests if json.loads(value["payload"]).get("mode") != "prepare"]
    mt = [value for value in records if value["task_kind"] == "translation"
          and json.loads(value["payload"]).get("mode") != "prepare"]
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


def test_parent_eof_preserves_terminal_raw_history_without_starting_queued_mt_inference(
    rust_api, monkeypatch, tmp_path,
):
    from langtextflow import rust_runtime
    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_api
    correction_pid = tmp_path / "correction.pid"
    mt_started = tmp_path / "mt-started"
    mt_pid = tmp_path / "mt.pid"
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
        mt = _MT.replace("    failed=", (
            f"    open({str(mt_started)!r},'w').write('inference')\n    failed="
        ))
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                {"program": sys.executable, "args": ["-u", "-c",
                 "import os\n" + f"open({str(mt_pid)!r},'w').write(str(os.getpid()))\n" + mt]})

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
    assert not _pid_alive(int(mt_pid.read_text()))  # Prepared child is also owned/reaped.


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


def test_actual_metrics_observes_native_jobs_not_idle_python_queues_and_preserves_final_reset(
    rust_api, monkeypatch,
):
    from langtextflow import rust_runtime

    client, runtime = rust_api
    original_worker = rust_runtime._python_worker
    correction = """
import json,sys
for line in sys.stdin:
    request=json.loads(line); payload=json.loads(request['payload'])
    reply={key:request[key] for key in ('protocol_version','request_id','session_id',
                                      'session_generation','segment_id','source_revision')}
    reply.update(status='ok',result=payload['text'],error=None)
    print(json.dumps(reply),flush=True)
"""

    def worker(module, args):
        if module == "langtextflow.correction_worker":
            return {"program": getattr(sys, "_base_executable", None) or sys.executable,
                    "args": ["-u", "-c", correction]}
        return original_worker(module, args)

    def commands(self, request):
        mt = _MT.replace("import json,sys", "import json,sys,time").replace(
            "    failed=", "    time.sleep(1.5)\n    failed=",
        )
        return ({"program": sys.executable, "args": ["-u", "-c", _ASR]},
                {"program": sys.executable, "args": ["-u", "-c", mt]})

    monkeypatch.setattr(rust_runtime, "_python_worker", worker)
    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    runtime.settings.translation_target_timeout_seconds = 2
    runtime.settings.correction_timeout_seconds = 2
    first = client.post("/api/v1/session/start", json=_REQUEST)
    assert first.status_code == 200, first.text
    first_sid = first.json()["session_id"]

    async def feed():
        for _ in range(3):
            await runtime.feed_audio(struct.pack("<f", .1) * 64000)

    client.portal.call(feed)
    deadline = time.monotonic() + 5
    while True:
        data = client.get("/api/v1/metrics").json()
        native = data["rust_authority"]
        sample = native["sample"]
        if (native["status"] == "fresh" and sample["translation"]["active"] == 1
                and sample["translation"]["overflow"] > 0):
            break
        assert time.monotonic() < deadline, data
        time.sleep(.01)
    assert data["runtime_core"] == "rust" and data["postprocess_queue_depth"] > 0
    assert runtime.pipeline.queue_depth == 0 and runtime._persistence_queue.qsize() == 0
    assert data["postprocess_segments_failed"] is None
    assert data["postprocess_queue_lag_p95_ms"] is None
    assert sample["correction"]["wait_samples"] > 0
    assert data["websocket_clients"] == 0 and sample["observer"]["connected"] == 1
    assert "Original English source" not in json.dumps(data)
    assert sample["session_id"] == first_sid and sample["session_generation"] == 1
    assert client.post("/api/v1/session/stop").status_code == 200
    final = client.get("/api/v1/metrics").json()
    assert final["rust_authority"]["status"] == "final"
    last = final["rust_authority"]["sample"]
    assert last["session_id"] == first_sid and last["sequence"] > sample["sequence"]
    assert last["final"] and last["stop_error"] is None and last["history"]["stopped"]
    assert last["translation"]["expired"] > 0
    assert last["translation"]["queued"] == last["translation"]["active"] == 0
    assert final["asr_queue_depth"] == final["postprocess_queue_depth"] == 0
    assert last["ingress"]["accepted_samples"] == last["ingress"]["processed_samples"] == 192000
    second = client.post("/api/v1/session/start", json=_REQUEST)
    assert second.status_code == 200, second.text
    second_sid = second.json()["session_id"]
    assert second_sid != first_sid
    reset = client.get("/api/v1/metrics").json()["rust_authority"]
    assert reset["status"] in {"not_sampled", "fresh"}
    if reset["sample"] is not None:
        assert reset["sample"]["session_id"] == second_sid
        assert reset["sample"]["translation"]["admitted"] == 0
        assert reset["sample"]["translation"]["wait_p95_ms"] is None
    assert client.post("/api/v1/session/stop").status_code == 200
    latest = client.get("/api/v1/metrics").json()["rust_authority"]
    assert latest["sample"]["session_id"] == second_sid


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
    native = """
import json,os,sys,time
open(sys.argv[1],'w').write(str(os.getpid()))
for line in sys.stdin:
    req=json.loads(line)
    if json.loads(req['payload']).get('mode')!='prepare':
        time.sleep(30)
        continue
    reply={k:req[k] for k in ('protocol_version','request_id','session_id',
                             'session_generation','segment_id','source_revision')}
    result={'asr_protocol_version':1,'prepared':True,'sample_rate':16000}
    payload=json.loads(req['payload'])
    if 'inference_options' in payload:result['inference_options']=payload['inference_options']
    reply.update(status='ok',error=None,result=json.dumps(result))
    print(json.dumps(reply),flush=True)
"""

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


def test_rust_opt_in_recording_matches_websocket_pcm_and_retains_private_asset_ownership(
    rust_recording_api,
):
    client, runtime = rust_recording_api
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "translation_provider": "none", "record_audio": True,
    })
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    bridge = runtime.engine
    assert client.get("/api/v1/session/recording").json()["active"]
    frames = [(16, -.5, -16384), (160, 0, 0), (1024, .25, 8192), (480, 1, 32767)]
    with (client.websocket_connect("/ws/captions") as captions,
          client.websocket_connect("/ws/audio") as audio):
        assert captions.receive_json()["type"] == "snapshot"
        assert audio.receive_json()["type"] == "audio_config"
        for count, value, _ in frames:
            audio.send_bytes(struct.pack("<f", value) * count)
        audio.send_text("end")
        event = captions.receive_json()
        while not event["committed"]:
            event = captions.receive_json()
    assert client.post("/api/v1/session/stop").status_code == 200
    expected = b"".join(struct.pack("<h", value) * count for count, _, value in frames)
    rows = assert_recordings(client, runtime, sid, [(expected, 0)])
    assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
    assert runtime._persistence_task is None and runtime.pipeline.queue_depth == 0
    assert runtime.history.get_session(sid).ended_at is not None
    assert not list(runtime.archive.audio_root.glob("*.partial"))
    next_session = client.post("/api/v1/session/start", json=_REQUEST)
    assert next_session.status_code == 200
    assert client.get("/api/v1/session/recording").json()["active"] is False
    wrong = client.get(
        f"/api/v1/history/{next_session.json()['session_id']}/recordings/"
        f"{rows[0]['recording_id']}/audio",
    )
    assert wrong.status_code == 404


def test_rust_late_record_stop_resume_uses_received_pcm_clock_without_caption_restart(
    rust_recording_api,
):
    client, runtime = rust_recording_api
    assert client.post("/api/v1/session/start", json=_REQUEST).status_code == 200
    sid, bridge = runtime.state.session_id, runtime.engine
    url = "/api/v1/session/recording"
    control = {"expected_session_id": sid}
    def feed(count, value):
        client.portal.call(runtime.feed_audio, struct.pack("<f", value) * count)
    feed(16017, .25)
    first_offset = 16017 * 1000 / 16000
    first = client.post(f"{url}/start", json=control)
    assert first.status_code == 200 and first.json()["start_offset_ms"] == first_offset
    assert client.post(f"{url}/stop", json={"expected_session_id": "stale"}).status_code == 409
    feed(8000, -.5)
    assert client.post(f"{url}/stop", json=control).status_code == 200
    assert runtime.state.running and runtime.engine is bridge and bridge.running
    feed(4001, 0)
    with pytest.raises(ValueError):
        client.portal.call(runtime.feed_audio, struct.pack("<f", float("nan")))
    second_offset = (16017 + 8000 + 4001) * 1000 / 16000
    second = client.post(f"{url}/start", json=control)
    assert second.status_code == 200 and second.json()["start_offset_ms"] == second_offset
    feed(12000, .75)
    assert client.post(f"{url}/stop", json=control).status_code == 200
    assert runtime.engine is bridge and bridge.running
    assert_recordings(client, runtime, sid, [
        (struct.pack("<h", -16384) * 8000, first_offset),
        (struct.pack("<h", 24575) * 12000, second_offset),
    ])
    client.portal.call(runtime.end_audio)
    assert client.post("/api/v1/session/stop").status_code == 200


@pytest.mark.parametrize("failure", ["open", "overflow"])
def test_rust_recording_storage_failure_never_stops_caption_authority(
    rust_recording_api, monkeypatch, failure,
):
    from langtextflow.recording_writer import SessionRecordingWriter

    client, runtime = rust_recording_api
    assert client.post("/api/v1/session/start", json=_REQUEST).status_code == 200
    sid, bridge = runtime.state.session_id, runtime.engine
    if failure == "open":
        def fail(_):
            raise OSError("private storage /path/token must not escape")
        monkeypatch.setattr(SessionRecordingWriter, "_open", fail)
    response = client.post("/api/v1/session/recording/start", json={"expected_session_id": sid})
    if failure == "open":
        assert response.status_code == 503 and "/path" not in response.text
    else:
        assert response.status_code == 200
        # Do not stall a native thread: queue pressure is produced on its owning loop.
        async def overflow():
            writer = runtime._recorder
            for _ in range(writer._queue.maxsize + 1):
                writer.offer(struct.pack("<f", .25))
        client.portal.call(overflow)
    client.portal.call(runtime.feed_audio, struct.pack("<f", .25) * 64000)
    client.portal.call(runtime.end_audio)
    saved = client.get("/api/v1/captions").json()
    assert saved and saved[0]["committed"] and saved[0]["translation_states"]["ko"] == "available"
    assert runtime.engine is bridge and not bridge.running
    assert bridge._process.returncode is None  # Caption/history owner survives input EOF.
    assert client.post("/api/v1/session/stop").status_code == 200
    assert runtime.archive.recordings(sid) == []
    assert not list(runtime.archive.audio_root.glob("*.partial"))
    assert not list(runtime.archive.audio_root.glob("*.wav"))


@pytest.mark.parametrize("failure", ["failed_start", "missing_history_owner"])
def test_rust_recording_never_opens_without_an_active_persisted_source(
    rust_recording_api, monkeypatch, failure,
):
    client, runtime = rust_recording_api

    def commands(self, request):
        if failure == "failed_start":
            return {"program": str(runtime.archive.audio_root / "missing-private-worker"),
                    "args": []}, None
        # A real deletion between bootstrap and Rust writer admission.
        with sqlite3.connect(runtime.settings.database_path) as conn:
            conn.execute("DELETE FROM sessions WHERE session_id=?", (self.session_id,))
        return {"program": sys.executable, "args": ["-u", "-c", _ASR]}, None

    monkeypatch.setattr(RustRuntimeBridge, "_worker_commands", commands)
    started = client.post("/api/v1/session/start", json=_REQUEST | {
        "record_audio": True, "translation_provider": "none",
    })
    assert started.status_code == (503 if failure == "failed_start" else 200)
    assert "missing-private-worker" not in started.text
    status = client.get("/api/v1/session/recording").json()
    assert not status["active"] and not status["available"]
    assert not list(runtime.archive.audio_root.iterdir())
    if failure == "missing_history_owner":
        assert runtime.state.persistence_error and runtime.state.recording_error
        assert client.post("/api/v1/session/recording/start", json={
            "expected_session_id": runtime.state.session_id,
        }).status_code == 409
        client.portal.call(runtime.feed_audio, struct.pack("<f", .25) * 1600)
        client.portal.call(runtime.end_audio)
        assert client.get("/api/v1/captions").json()[0]["committed"]
        assert client.post("/api/v1/session/stop").status_code == 200
    else:
        # Confirmed failed-start cleanup permits retry; recording still needs
        # a successfully prepared, persisted and active source.
        assert runtime._rust_slot._value == 1 and runtime.engine is None


@pytest.mark.parametrize("action", ["start", "stop"])
def test_rust_recording_cancelled_http_control_keeps_io_owned_before_next_source(
    rust_recording_api, monkeypatch, action,
):
    from langtextflow.recording_writer import SessionRecordingWriter

    client, runtime = rust_recording_api
    assert client.post("/api/v1/session/start", json=_REQUEST).status_code == 200
    sid, bridge = runtime.state.session_id, runtime.engine
    entered, release = Event(), Event()
    handles = []
    method = "_open" if action == "start" else "_close_file"
    original = getattr(SessionRecordingWriter, method)
    if action == "stop":
        assert client.post("/api/v1/session/recording/start", json={
            "expected_session_id": sid,
        }).status_code == 200
        client.portal.call(runtime.feed_audio, struct.pack("<f", .25) * 3200)

    def gated(writer):
        if action == "start":
            original(writer)
        handles.append(writer._stream)
        entered.set()
        assert release.wait(5)
        if action == "stop":
            return original(writer)

    monkeypatch.setattr(SessionRecordingWriter, method, gated)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 50000)),
            base_url="http://localhost", headers=_ORIGIN,
        ) as http:
            task = asyncio.create_task(http.post(
                f"/api/v1/session/recording/{action}", json={"expected_session_id": sid},
            ))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                await runtime.feed_audio(struct.pack("<f", .25) * 1600)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                replacement = asyncio.create_task(runtime.start(StartSessionRequest(**_REQUEST)))
                await asyncio.sleep(.02)
                assert not replacement.done() and runtime.engine is bridge and bridge.running
                assert not handles[0].closed
                release.set()
                await asyncio.wait_for(replacement, 8)
                assert handles[0].closed and bridge._process.returncode == 0
            finally:
                release.set()

    client.portal.call(scenario)
    assert len(runtime.archive.recordings(sid)) == (1 if action == "stop" else 0)
    assert not list(runtime.archive.audio_root.glob("*.partial"))
    assert not runtime.recording_status()["active"] and runtime._persistence_task is None


@pytest.mark.parametrize("interrupted_promotion", [False, True])
def test_rust_parent_eof_finalizes_owned_media_and_journal_recovery_preserves_observation(
    rust_recording_api, monkeypatch, interrupted_promotion,
):
    from langtextflow.asr.base import AsrEngineError

    client, runtime = rust_recording_api
    assert client.post("/api/v1/session/start", json=_REQUEST | {
        "record_audio": True,
    }).status_code == 200
    sid, bridge = runtime.state.session_id, runtime.engine
    client.portal.call(runtime.feed_audio, struct.pack("<f", -.5) * 1600)
    if interrupted_promotion:
        def interrupted(*args, **kwargs):
            raise OSError("owned promotion boundary")
        monkeypatch.setattr(runtime.archive.recording_journal, "promote", interrupted)

    async def eof():
        bridge._process.stdin.close()
        await asyncio.wait_for(bridge._process.wait(), 8)
        await asyncio.wait_for(bridge._reader, 2)
        assert not runtime.recording_status()["available"]
        with pytest.raises(AsrEngineError):
            await runtime.stop()

    client.portal.call(eof)
    assert not runtime.recording_status()["active"]
    assert runtime.history.get_session(sid).ended_at is not None
    if interrupted_promotion:
        assert runtime.archive.recordings(sid) == []
        with runtime.archive._connect() as conn:
            before = dict(conn.execute("SELECT * FROM recording_journal").fetchone())

        async def recover():
            restarted = CaptionRuntime(runtime.settings)
            try:
                await restarted.initialize()
                await asyncio.wait_for(restarted._recording_recovery_task, 5)
            finally:
                await restarted.shutdown()

        client.portal.call(recover)
        restored = runtime.archive.recordings(sid)[0]
        assert restored["recording_id"] == before["recording_id"]
        assert restored["finalized_at"] == before["created_at"]
    assert_recordings(client, runtime, sid, [(struct.pack("<h", -16384) * 1600, 0)])
    assert not list(runtime.archive.audio_root.glob("*.partial"))
    assert bridge._process.returncode == 0 and runtime._rust_slot._value == 1
