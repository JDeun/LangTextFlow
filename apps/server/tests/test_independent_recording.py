"""Independent PCM sink controls, without microphone/model/download assumptions."""

from __future__ import annotations

import asyncio
import struct
import threading
import wave

import httpx
import pytest
from fastapi import FastAPI
from test_recording_pcm_clock import SampleClockAsr

from langtextflow.config import Settings
from langtextflow.models import StartSessionRequest
from langtextflow.post_event_api import install_post_event_routes
from langtextflow.recording_api import install_recording_routes
from langtextflow.recording_writer import SessionRecordingWriter, pcm_float32_to_pcm16
from langtextflow.runtime import CaptionRuntime, SessionChangedError


async def start(tmp_path, monkeypatch):
    settings = Settings(database_path=str(tmp_path / "independent.db"), storage_reserve_mb=64)
    runtime = CaptionRuntime(settings)
    engines = []

    def engine(**kwargs):
        result = SampleClockAsr()
        engines.append(result)
        return result

    monkeypatch.setattr(runtime, "_faster_whisper_engine", engine)
    request = StartSessionRequest(
        engine="faster-whisper",
        correction_provider="none",
        translation_provider="none",
        record_audio=False,
    )
    state = await runtime.start(request)
    assert not state.recording_enabled and runtime.recording_status()["phase"] == "idle"
    return runtime, state.session_id, request, engines


def pcm(count, value=0.25):
    return struct.pack("<f", value) * count


@pytest.mark.asyncio
async def test_late_stop_resume_preserves_absolute_pcm_clock_wav_bytes_and_restart(
    tmp_path, monkeypatch
):
    runtime, sid, _, engines = await start(tmp_path, monkeypatch)
    try:
        prefix, first, gap, second = pcm(16017), pcm(8000, -0.5), pcm(4001, 0), pcm(12000, 0.75)
        await runtime.feed_audio(prefix)
        result = await runtime.control_recording("start", sid)
        first_offset = 16017 * 1000 / 16000
        assert result["active"] and result["start_offset_ms"] == first_offset
        await runtime.feed_audio(first)
        saved = await runtime.control_recording("stop", sid)
        assert not saved["active"] and saved["recording"]["start_offset_ms"] == first_offset
        assert runtime.state.running and engines[0].running
        await asyncio.sleep(0.02)  # A wall-clock pause supplies no missing PCM.
        await runtime.feed_audio(gap)
        with pytest.raises(ValueError):
            await runtime.feed_audio(struct.pack("<f", float("nan")))
        resumed = await runtime.control_recording("start", sid)
        second_offset = (16017 + 8000 + 4001) * 1000 / 16000
        assert resumed["start_offset_ms"] == second_offset
        await runtime.feed_audio(second)
        await runtime.control_recording("stop", sid)
        assert engines[0].accepted == [prefix, first, gap, second] and runtime.state.running
        await runtime.stop()
        recordings = runtime.archive.recordings(sid)
        assert [r["start_offset_ms"] for r in recordings] == [first_offset, second_offset]
        for row, expected in zip(recordings, [first, second], strict=True):
            path = runtime.archive.recording_path(sid, row["recording_id"])
            with wave.open(str(path), "rb") as saved_wave:
                assert saved_wave.getframerate() == 16000
                assert saved_wave.readframes(saved_wave.getnframes()) == pcm_float32_to_pcm16(
                    expected
                )
        restarted = CaptionRuntime(runtime.settings)
        await restarted.initialize()
        try:
            assert restarted.archive.recordings(sid) == recordings
            assert not restarted.recording_status()["active"] and not restarted.state.running
        finally:
            await restarted.shutdown()
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_stale_controls_never_touch_another_session_or_current_audio(tmp_path, monkeypatch):
    runtime, sid, _, engines = await start(tmp_path, monkeypatch)
    try:
        for action in ["start", "stop"]:
            with pytest.raises(SessionChangedError):
                await runtime.control_recording(action, "old-session")
        assert not list(runtime.archive.audio_root.iterdir())
        await runtime.control_recording("start", sid)
        writer = runtime._recorder
        with pytest.raises(SessionChangedError):
            await runtime.control_recording("stop", "other-session")
        await runtime.feed_audio(pcm(1000))
        assert runtime._recorder is writer and engines[0].samples == 1000
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_repeated_start_cancellation_closes_late_fd_before_next_session(
    tmp_path, monkeypatch
):
    runtime, sid, request, engines = await start(tmp_path, monkeypatch)
    opened, release = threading.Event(), threading.Event()
    original = SessionRecordingWriter._open
    handles = []

    def slow(writer):
        original(writer)
        handles.append(writer._stream)
        opened.set()
        assert release.wait(5)

    monkeypatch.setattr(SessionRecordingWriter, "_open", slow)
    try:
        operation = asyncio.create_task(runtime.control_recording("start", sid))
        assert await asyncio.to_thread(opened.wait, 5)
        await runtime.feed_audio(pcm(1600))
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        restart = asyncio.create_task(runtime.start(request))
        await asyncio.sleep(0.02)
        assert not restart.done() and engines[0].running and len(engines) == 1
        assert not handles[0].closed
        release.set()
        await asyncio.wait_for(restart, 5)
        assert handles[0].closed and not list(runtime.archive.audio_root.iterdir())
        assert runtime.state.session_id != sid and not runtime.recording_status()["active"]
    finally:
        release.set()
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancelled_stop_keeps_finalizer_owned_captions_running_and_restart_waits(
    tmp_path, monkeypatch
):
    runtime, sid, request, engines = await start(tmp_path, monkeypatch)
    closing, release = threading.Event(), threading.Event()
    original = SessionRecordingWriter._close_file

    def slow(writer):
        closing.set()
        assert release.wait(5)
        return original(writer)

    monkeypatch.setattr(SessionRecordingWriter, "_close_file", slow)
    try:
        await runtime.feed_audio(pcm(1600))
        await runtime.control_recording("start", sid)
        writer = runtime._recorder
        await runtime.feed_audio(pcm(3200))
        operation = asyncio.create_task(runtime.control_recording("stop", sid))
        assert await asyncio.to_thread(closing.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        await runtime.feed_audio(pcm(800))
        assert engines[0].samples == 5600 and runtime.state.running and writer.frames == 3200
        restart = asyncio.create_task(runtime.start(request))
        await asyncio.sleep(0.02)
        assert not restart.done() and writer._stream is not None
        release.set()
        await asyncio.wait_for(restart, 5)
        assert writer._stream is None and len(runtime.archive.recordings(sid)) == 1
        assert runtime.archive.recordings(sid)[0]["start_offset_ms"] == 100
    finally:
        release.set()
        await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop", "replacement-start"])
async def test_source_stop_cancellation_finishes_history_hub_and_native_before_restart(
    tmp_path,
    monkeypatch,
    action,
):
    runtime, sid, request, engines = await start(tmp_path, monkeypatch)
    closing, release = threading.Event(), threading.Event()
    original_close = SessionRecordingWriter._close_file
    original_hub = runtime.hub.close_all
    events = []

    def slow(writer):
        closing.set()
        assert release.wait(5)
        return original_close(writer)

    async def hub(*args, **kwargs):
        events.append(kwargs.get("reason"))
        await original_hub(*args, **kwargs)

    monkeypatch.setattr(SessionRecordingWriter, "_close_file", slow)
    monkeypatch.setattr(runtime.hub, "close_all", hub)
    try:
        await runtime.control_recording("start", sid)
        writer = runtime._recorder
        await runtime.feed_audio(pcm(3200))
        operation = asyncio.create_task(
            runtime.stop() if action == "stop" else runtime.start(request)
        )
        assert await asyncio.to_thread(closing.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        release.set()
        next_session = await asyncio.wait_for(runtime.start(request), 5)
        assert next_session.session_id != sid
        assert runtime.history.get_session(sid).ended_at is not None
        assert writer._stream is None and not engines[0].running
        assert events.index("caption session stopped") < len(events) - 1
        assert events[-1] == "caption session starting"
    finally:
        release.set()
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_disk_failure_preserves_live_caption_source(tmp_path, monkeypatch):
    runtime, sid, _, engines = await start(tmp_path, monkeypatch)

    def denied(writer):
        raise OSError("owned disk failure /private/path must not be exposed")

    monkeypatch.setattr(SessionRecordingWriter, "_open", denied)
    try:
        status = await runtime.control_recording("start", sid)
        assert status["phase"] == "error" and "/private" not in status["error"]
        await runtime.feed_audio(pcm(1600))
        assert engines[0].samples == 1600 and runtime.state.running
        assert runtime.archive.recordings(sid) == []
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_start_deadline_keeps_late_open_owned_until_it_is_closed(tmp_path, monkeypatch):
    runtime, sid, _, engines = await start(tmp_path, monkeypatch)
    opened, release = threading.Event(), threading.Event()
    original = SessionRecordingWriter._open
    handles = []

    def slow(writer):
        original(writer)
        handles.append(writer._stream)
        opened.set()
        assert release.wait(5)

    monkeypatch.setattr(SessionRecordingWriter, "_open", slow)
    runtime.settings.session_start_timeout_seconds = 0.02
    try:
        with pytest.raises(TimeoutError):
            await runtime.control_recording("start", sid)
        assert opened.is_set() and not handles[0].closed
        with pytest.raises(SessionChangedError):
            await runtime.control_recording("start", sid)
        await runtime.feed_audio(pcm(1600))
        assert engines[0].samples == 1600 and runtime.state.running
        release.set()
        await asyncio.gather(runtime._recording_control, return_exceptions=True)
        assert handles[0].closed and not list(runtime.archive.audio_root.iterdir())
    finally:
        release.set()
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_late_recording_offset_survives_failed_promotion_and_restart_recovery(
    tmp_path, monkeypatch
):
    runtime, sid, _, _ = await start(tmp_path, monkeypatch)
    try:
        await runtime.feed_audio(pcm(16000))
        await runtime.control_recording("start", sid)
        await runtime.feed_audio(pcm(8000))

        def crash(*args, **kwargs):
            raise OSError("owned promotion interruption")

        monkeypatch.setattr(runtime.archive.recording_journal, "promote", crash)
        stopped = await runtime.control_recording("stop", sid)
        assert stopped["phase"] == "error" and runtime.archive.recordings(sid) == []
        await runtime.stop()
        restarted = CaptionRuntime(runtime.settings)
        await restarted.initialize()
        try:
            await asyncio.wait_for(restarted._recording_recovery_task, 5)
            restored = restarted.archive.recordings(sid)
            assert len(restored) == 1 and restored[0]["start_offset_ms"] == 1000
            assert restored[0]["duration_seconds"] == 0.5 and restored[0]["status"] == "completed"
        finally:
            await restarted.shutdown()
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_actual_routes_operator_guard_status_and_download_before_caption_stop(
    tmp_path, monkeypatch
):
    from langtextflow import main

    runtime, sid, _, _ = await start(tmp_path, monkeypatch)
    app = FastAPI()
    app.middleware("http")(main.reject_remote_operator_api_before_body_parse)
    install_recording_routes(app, runtime, main._require_operator)
    shutdown = install_post_event_routes(
        app, runtime, runtime.archive, runtime.settings, main._require_operator
    )
    try:
        sent = False

        async def private_body():
            nonlocal sent
            sent = True
            yield b"unauthorized private recording request"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 1234)),
            base_url="http://test",
        ) as remote:
            assert (
                await remote.post("/api/v1/session/recording/start", content=private_body())
            ).status_code == 403
            assert (await remote.get("/api/v1/session/recording")).status_code == 403
            assert not sent
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://test",
        ) as local:
            assert not (await local.get("/api/v1/session/recording")).json()["active"]
            assert (await local.post("/api/v1/session/recording/start", json={})).status_code == 422
            assert (
                await local.post(
                    "/api/v1/session/recording/start", json={"expected_session_id": "wrong"}
                )
            ).status_code == 409
            assert (
                await local.post(
                    "/api/v1/session/recording/start", json={"expected_session_id": sid}
                )
            ).json()["active"]
            await runtime.feed_audio(pcm(1600))
            stopped = await local.post(
                "/api/v1/session/recording/stop", json={"expected_session_id": sid}
            )
            assert stopped.status_code == 200 and runtime.state.running
            asset = stopped.json()["recording"]
            audio = await local.get(
                f"/api/v1/history/{sid}/recordings/{asset['recording_id']}/audio",
                headers={"Range": "bytes=0-43"},
            )
            assert audio.status_code == 206 and audio.content.startswith(b"RIFF")
            assert (
                runtime.state.running
                and (await local.get("/api/v1/session/recording")).json()["phase"] == "idle"
            )
    finally:
        await runtime.shutdown()
        await shutdown()
