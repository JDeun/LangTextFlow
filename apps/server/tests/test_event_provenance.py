"""Production event wiring; provider responses are fixtures, not model inference."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_session_repository import _request, _state

from langtextflow import llm_correction, main
from langtextflow.asr.base import AsrEngine
from langtextflow.config import Settings
from langtextflow.exports import export_json
from langtextflow.models import (
    AsrProvenance,
    CaptionStage,
    CorrectionProvenance,
    StartSessionRequest,
    TranscriptEvent,
    TranslationProvenance,
)
from langtextflow.runtime import CaptionRuntime
from langtextflow.session_repository import SessionRepository
from langtextflow.translation import ollama


@pytest.mark.parametrize("private", [
    "/home/operator/private/model", "C:\\Users\\operator\\model", "C:private",
    "~/models/foo", "../models/foo", "file:///secret/model", "https://host/model?key=secret",
    "sk-private-api-token", "hf_private-token", "ghp_private-token",
    "host/model?token=secret", "user:password@host/model", "model\nsecret", "x" * 201,
])
def test_private_model_locations_are_unknown_in_public_provenance(private):
    asr = AsrProvenance(provider="faster-whisper", configured_model=private,
                        verified_model=private, model_revision=private)
    mt = TranslationProvenance(provider="ollama", requested_model=private,
                               configured_model=private, verified_model=private,
                               model_revision=private)
    assert set(asr.model_dump(include={"configured_model", "verified_model", "model_revision"})
               .values()) == {None}
    assert set(mt.model_dump(exclude={"provider"}).values()) == {None}
    correction = CorrectionProvenance(
        method="fallback", provider="ollama", model=private,
        fallback_reason=f"provider error {private}",
    )
    assert correction.model is None
    assert correction.fallback_reason == "correction provider failed; using deterministic result"


def test_provenance_is_bounded_and_does_not_accept_an_alignment_claim():
    for value in ("small", "large-v3", "facebook/m2m100_418M", "qwen3.5:4b"):
        assert AsrProvenance(provider="test", configured_model=value).configured_model == value
    for payload in (
        {"provider": "https://host?secret"}, {"sample_rate": 0},
        {"audio_chunk_start_sample": 10, "audio_chunk_end_sample": 9},
        {"timeline_offset_samples": 2**53}, {"auto_generation": 1_000_001},
        {"timestamp_basis": "native_aligned"},
    ):
        with pytest.raises(ValidationError):
            AsrProvenance(**({"provider": "test"} | payload))
    event = source(1)
    assert event.asr_provenance.verified_model is None
    with pytest.raises(ValidationError):
        TranscriptEvent(**(event.model_dump() | {"translation_provenance": {
            f"lang{i}": TranslationProvenance(provider="demo") for i in range(11)
        }}))


def source(revision: int) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="revised", version=revision, source_revision=revision,
        stage=CaptionStage.STABLE, source_language="ko", text=f"원문 {revision}",
        start_ms=0, end_ms=1000,
        asr_provenance=AsrProvenance(
            provider="faster-whisper", configured_model="/private/operator/models/small",
            timestamp_basis="sample_clock_native_hint", sample_rate=16000,
            audio_chunk_start_sample=0, audio_chunk_end_sample=16000 * revision,
        ),
    )


class PassiveAsr(AsrEngine):
    """Supply explicit source revisions without starting native inference."""
    running = False
    accepts_audio = True
    sample_rate = 16000

    async def start(self, request):
        self.running = True

    async def stop(self):
        self.running = False

    async def feed_audio(self, frame):
        pass

    async def end_audio(self):
        pass


def test_revision_partial_mt_failure_actual_audience_ws_history_and_reconnect(
    monkeypatch, tmp_path,
):
    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "events.db"), ollama_translation_model="qwen3.5:4b",
        ollama_url="http://private-provider.invalid:11434", translation_max_parallel_targets=2,
        ollama_correction_model="/private/operator/checkpoint",
    ))
    started = asyncio.Event()
    cancelled = asyncio.Event()
    submitted = []

    async def provider_response(client, method, url, **kwargs):
        if method == "GET":
            return {"models": [{"name": "qwen3.5:4b"}, {"name": "/private/operator/checkpoint"}]}
        payload = kwargs["json"]
        if payload["model"] == "/private/operator/checkpoint":
            raise RuntimeError("http://user:secret@private-provider.invalid/chat?token=secret "
                               "/private/operator/checkpoint")
        submitted.append(payload["model"])
        if "원문 1" in payload["messages"][-1]["content"]:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        if "(ja)" in payload["messages"][0]["content"]:
            raise RuntimeError("fixture target failure")
        return {"message": {"content": "revised English"}}

    @asynccontextmanager
    async def lifespan(app):
        await runtime.initialize()
        try:
            yield
        finally:
            await runtime.shutdown()

    monkeypatch.setattr(ollama, "bounded_json_request", provider_response)
    monkeypatch.setattr(llm_correction, "bounded_json_request", provider_response)
    monkeypatch.setattr(
        runtime, "_build_engine", lambda request: PassiveAsr(runtime.pipeline.ingest),
    )
    monkeypatch.setattr(main, "runtime", runtime)
    monkeypatch.setattr(main.app.router, "lifespan_context", lifespan)

    with TestClient(main.app, client=("127.0.0.1", 50000)) as client:
        state = client.portal.call(runtime.start, StartSessionRequest(
            engine="faster-whisper", source_language="ko", target_languages=["en", "ja"],
            translation_provider="ollama", translation_model=None, correction_provider="ollama",
        ))
        with client.websocket_connect(f"/ws/audience/{state.join_code}") as websocket:
            assert websocket.receive_json() == {"type": "snapshot", "segments": []}
            client.portal.call(runtime.engine.publish, source(1))

            async def revise():
                await asyncio.wait_for(started.wait(), 2)
                await runtime.engine.publish(source(2))
                await asyncio.wait_for(runtime.pipeline._queue.join(), 2)
                await asyncio.wait_for(runtime._persistence_queue.join(), 2)

            client.portal.call(revise)
            received = []
            for _ in range(12):
                event = websocket.receive_json()
                received.append(event)
                if event["committed"]:
                    break
            final = received[-1]
            assert cancelled.is_set() and final["source_revision"] == 2 and final["committed"]
            assert final["translations"] == {"en": "revised English"}
            assert final["translation_states"] == {"en": "available", "ja": "failed"}
            assert {event["stage"] for event in received if event["source_revision"] == 2} == {
                "stable", "corrected", "translated", "committed",
            }
            for event in received:
                assert event["asr_provenance"]["configured_model"] is None
                assert event["asr_provenance"]["timestamp_basis"] == "sample_clock_native_hint"
                assert event["asr_provenance"]["audio_chunk_end_sample"] == (
                    16000 * event["source_revision"]
                )
                if event["correction"] is not None:
                    assert event["correction"]["model"] is None
                    assert event["correction"]["fallback_reason"] == (
                        "correction provider failed; using deterministic result"
                    )
                assert set(event["translation_provenance"]) == {"en", "ja"}
                for provenance in event["translation_provenance"].values():
                    assert provenance == {
                        "provider": "ollama", "requested_model": None,
                        "configured_model": "qwen3.5:4b", "verified_model": None,
                        "model_revision": None,
                    }
            assert "private-provider" not in json.dumps(received)
            assert "/private/operator" not in json.dumps(received)
            assert "secret" not in json.dumps(received)
            assert "token=" in runtime.pipeline.correction_status.error
        with client.websocket_connect(f"/ws/audience/{state.join_code}") as reconnected:
            assert reconnected.receive_json()["segments"] == [final]
        assert client.get(f"/api/v1/audience/{state.join_code}/captions").json() == [final]
        assert client.get(f"/api/v1/history/{state.session_id}/captions").json() == [final]
        client.portal.call(runtime.stop)
        restored = SessionRepository(runtime.settings.database_path)
        restored.initialize()
        stored = restored.segments(state.session_id)
        assert [event.model_dump(mode="json") for event in stored] == [final]
        exported = json.loads(export_json(restored.get_session(state.session_id), stored))
        assert exported["segments"] == [final]
        assert submitted and set(submitted) == {"qwen3.5:4b"}


def test_existing_database_migrates_without_inventing_provenance(tmp_path):
    path = str(tmp_path / "legacy.db")
    repository = SessionRepository(path)
    repository.initialize()
    repository.create_session(_state(), _request())
    original = source(1).model_copy(update={"asr_provenance": None})
    repository.upsert_segment("session-1", original)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE transcript_segments DROP COLUMN asr_provenance_json")
        connection.execute(
            "ALTER TABLE transcript_segments DROP COLUMN translation_provenance_json"
        )
    repository.initialize()
    restored = repository.segments("session-1")[0]
    assert restored == original.model_copy(update={"original_text": original.text})
    assert restored.asr_provenance is None and restored.translation_provenance == {}
    repository.upsert_segment("session-1", source(2))
    assert repository.segments("session-1")[0].asr_provenance == source(2).asr_provenance
    # Old asynchronous revisions cannot rewrite the newer source's metadata.
    repository.upsert_segment("session-1", source(1).model_copy(update={"version": 20}))
    assert repository.segments("session-1")[0].asr_provenance == source(2).asr_provenance
