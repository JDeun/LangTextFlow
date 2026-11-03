"""Rejected session starts must preserve the actual admitted microphone socket."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from threading import Event

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from langtextflow import main
from langtextflow.config import Settings
from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.runtime import CaptionRuntime

_ORIGIN = {"origin": "http://localhost:5173"}
_START = {
    "engine": "faster-whisper", "correction_provider": "none", "translation_provider": "none",
}


class AudioEngine:
    accepts_audio = True
    sample_rate = 16000
    queue_depth = 0
    queue_capacity = 0
    failure = None

    def __init__(self):
        self.running = False
        self.frames = []
        self.received = Event()
        self.stops = 0

    async def start(self, request):
        self.request = request
        self.running = True

    async def stop(self):
        self.running = False
        self.stops += 1

    async def feed_audio(self, frame):
        self.frames.append(frame)
        text = f"source frame {len(self.frames)}"
        await self.publish(TranscriptEvent(
            segment_id=f"frame-{len(self.frames)}", version=1, stage=CaptionStage.STABLE,
            text=text, original_text=text, source_language=self.request.source_language,
            start_ms=len(self.frames) - 1, end_ms=len(self.frames),
        ))
        self.received.set()

    async def end_audio(self):
        pass


@pytest.fixture
def live_audio_api(monkeypatch, tmp_path):
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "session.db")))
    engines = []

    def build(**kwargs):
        engine = AudioEngine()
        engines.append(engine)
        return engine

    @asynccontextmanager
    async def lifespan(app):
        await runtime.initialize()
        try:
            yield
        finally:
            await runtime.shutdown()

    monkeypatch.setattr(runtime, "_faster_whisper_engine", build)
    monkeypatch.setattr(main, "runtime", runtime)
    monkeypatch.setattr(main, "_with_saved_glossary", lambda request: request)
    monkeypatch.setattr(main, "_audio_transition_lock", asyncio.Lock())
    monkeypatch.setattr(main, "_active_audio_socket", None)
    monkeypatch.setattr(main, "_audio_socket_accepting", True)
    monkeypatch.setattr(main.app.router, "lifespan_context", lifespan)
    with TestClient(main.app, client=("127.0.0.1", 50000), headers=_ORIGIN) as client:
        response = client.post("/api/v1/session/start", json=_START)
        assert response.status_code == 200
        yield client, runtime, engines, response.json()


def send_and_persist(client, websocket, runtime, engine):
    engine.received.clear()
    websocket.send_bytes(bytes(4))
    assert engine.received.wait(1), "existing microphone no longer feeds the live ASR"

    async def flush():
        await asyncio.wait_for(runtime.pipeline._queue.join(), 1)
        await asyncio.wait_for(runtime._persistence_queue.join(), 1)

    client.portal.call(flush)


@pytest.mark.parametrize(("invalid", "status"), [
    ({"project_id": "missing-project"}, 400),
    ({"translation_provider": "unknown-provider"}, 400),
    ({"correction_provider": "unknown-provider"}, 400),
    ({"translation_provider": "m2m100-ct2", "source_language": "en",
      "target_languages": ["fr"]}, 400),
    ({"engine": "unknown-engine"}, 400),
    ({"engine": "mock", "record_audio": True}, 400),
    ({"target_languages": "malformed-list"}, 422),
])
def test_rejected_start_preserves_audio_and_source_history(live_audio_api, invalid, status):
    client, runtime, engines, state = live_audio_api
    engine = engines[0]
    with client.websocket_connect(
        f"/ws/audio?expected_session_id={state['session_id']}", headers=_ORIGIN,
    ) as websocket:
        assert websocket.receive_json()["type"] == "audio_config"
        admitted_socket = main._active_audio_socket
        send_and_persist(client, websocket, runtime, engine)
        response = client.post("/api/v1/session/start", json={**_START, **invalid})
        assert response.status_code == status
        assert runtime.state.session_id == state["session_id"] and runtime.state.running
        assert runtime.engine is engine and engine.running and engine.stops == 0
        assert main._active_audio_socket is admitted_socket and main._audio_socket_accepting
        send_and_persist(client, websocket, runtime, engine)
        assert len(engine.frames) == 2
        saved = runtime.history.segments(state["session_id"])
        assert {row.original_text for row in saved if row.committed} == {
            "source frame 1", "source frame 2",
        }
        assert runtime.history.get_session(state["session_id"]).ended_at is None
        with pytest.raises(WebSocketDisconnect) as rejected, client.websocket_connect(
            f"/ws/audio?expected_session_id={state['session_id']}", headers=_ORIGIN,
        ):
            pass
        assert rejected.value.code == 4409


def test_valid_replacement_closes_old_socket_and_admits_only_new_session(live_audio_api):
    client, runtime, engines, state = live_audio_api
    with client.websocket_connect(
        f"/ws/audio?expected_session_id={state['session_id']}", headers=_ORIGIN,
    ) as websocket:
        assert websocket.receive_json()["type"] == "audio_config"
        replacement = client.post("/api/v1/session/start", json=_START)
        assert replacement.status_code == 200
        current = replacement.json()
        assert current["session_id"] != state["session_id"]
        assert websocket.receive()["code"] == 1012
        assert engines[0].stops == 1 and not engines[0].running
        assert runtime.history.get_session(state["session_id"]).ended_at is not None
    with pytest.raises(WebSocketDisconnect) as rejected, client.websocket_connect(
        f"/ws/audio?expected_session_id={state['session_id']}", headers=_ORIGIN,
    ):
        pass
    assert rejected.value.code == 4409
    with client.websocket_connect(
        f"/ws/audio?expected_session_id={current['session_id']}", headers=_ORIGIN,
    ) as websocket:
        assert websocket.receive_json()["type"] == "audio_config"
        send_and_persist(client, websocket, runtime, engines[-1])
