"""Opt-in live recording: realtime ASR isolation, durable WAV, loss semantics."""
from __future__ import annotations

import struct
import wave
from array import array
from datetime import UTC, datetime

import pytest

from langtextflow.config import Settings
from langtextflow.models import SessionState, StartSessionRequest
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.recording_writer import (
    SessionRecordingWriter,
    pcm_float32_to_pcm16,
)
from langtextflow.runtime import CaptionRuntime
from langtextflow.session_repository import SessionRepository


def _archive(tmp_path):
    path = str(tmp_path / "records.db")
    history = SessionRepository(path)
    history.initialize()
    history.create_session(
        SessionState(
            session_id="session-1", join_code="ABC123", engine="mock",
            started_at=datetime.now(UTC),
        ),
        StartSessionRequest(
            source_language="ko", target_languages=["en"], engine="mock",
        ),
    )
    archive = PostEventArchive(path)
    archive.initialize()
    return archive


@pytest.mark.asyncio
async def test_recording_writer_saves_single_valid_session_scoped_wav(tmp_path) -> None:
    archive = _archive(tmp_path)
    writer = SessionRecordingWriter(
        archive, session_id="session-1", sample_rate=16000, reserve_bytes=0,
    )
    await writer.start()
    pcm = struct.pack("<ffff", -1.0, -0.5, 0.5, 1.0)
    writer.offer(pcm)
    result = await writer.stop()
    assert result is not None
    assert writer.error is None
    assert result["duration_seconds"] == pytest.approx(4 / 16000)
    saved = archive.recording_path("session-1", result["recording_id"])
    assert saved is not None
    with wave.open(str(saved), "rb") as audio:
        assert audio.getframerate() == 16000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getnframes() == 4
        samples = array("h")
        samples.frombytes(audio.readframes(4))
        assert list(samples) == [-32767, -16384, 16384, 32767]
    assert not list(archive.audio_root.glob("*.partial"))


@pytest.mark.asyncio
async def test_invalid_recorder_input_never_persists_incomplete_media(tmp_path) -> None:
    archive = _archive(tmp_path)
    writer = SessionRecordingWriter(
        archive, session_id="session-1", sample_rate=16000, reserve_bytes=0,
    )
    await writer.start()
    writer.offer(b"malformed")  # 9 bytes: not divisible by 4 for float32 PCM
    assert await writer.stop() is None
    assert writer.error is not None
    assert archive.recordings("session-1") == []
    assert list(archive.audio_root.glob("*.partial")) == []


@pytest.mark.asyncio
async def test_nan_recorder_frame_never_creates_a_saved_recording(tmp_path) -> None:
    archive = _archive(tmp_path)
    writer = SessionRecordingWriter(
        archive, session_id="session-1", sample_rate=16000, reserve_bytes=0,
    )
    await writer.start()
    writer.offer(struct.pack("<f", float("nan")))
    assert await writer.stop() is None
    assert writer.error is not None
    assert archive.recordings("session-1") == []
    assert list(archive.audio_root.glob("*.partial")) == []


@pytest.mark.asyncio
async def test_queue_overflow_drops_recording_not_live_caption(tmp_path) -> None:
    archive = _archive(tmp_path)
    writer = SessionRecordingWriter(
        archive, session_id="session-1", sample_rate=16000,
        reserve_bytes=0, queue_chunks=1,
    )
    await writer.start()
    writer.offer(struct.pack("<f", 0.3))
    # No await/yield: consumer cannot drain while producer fills the queue.
    writer.offer(struct.pack("<f", 0.4))
    assert "queue overflow" in (writer.error or "")
    assert await writer.stop() is None
    assert archive.recordings("session-1") == []


def test_float_pcm_conversion_rejects_invalid_samples() -> None:
    with pytest.raises(ValueError):
        pcm_float32_to_pcm16(b"\x00")
    with pytest.raises(ValueError, match="non-finite"):
        pcm_float32_to_pcm16(struct.pack("<f", float("nan")))


@pytest.mark.asyncio
async def test_real_audio_is_mirrored_without_blocking_asr(tmp_path, monkeypatch) -> None:
    class FakeAudioEngine:
        accepts_audio = True
        sample_rate = 16000
        queue_depth = 0
        queue_capacity = 32
        failure = None
        running = False

        async def start(self, request):
            self.running = True

        async def feed_audio(self, pcm):
            assert pcm

        async def end_audio(self):
            pass

        async def stop(self):
            self.running = False

    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "runtime.db"), storage_reserve_mb=64,
    ))
    monkeypatch.setattr(runtime, "_build_engine", lambda _: FakeAudioEngine())
    live = await runtime.start(StartSessionRequest(
        record_audio=True,
        source_language="ko", target_languages=["en"],
        engine="mock", translation_provider="none",
    ))
    assert live.running is True
    assert live.recording_enabled is True
    frame = struct.pack("<" + "f" * 320, *([0.25] * 320))
    try:
        await runtime.feed_audio(frame)
        assert runtime.metrics.audio_frames_received == 1
    finally:
        stopped = await runtime.stop()
    assert stopped.recording_error is None
    records = runtime.archive.recordings(live.session_id)
    assert len(records) == 1
    assert records[0]["duration_seconds"] == pytest.approx(320 / 16000)
    assert runtime.history.get_session(live.session_id).ended_at is not None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_recording_requires_real_audio_and_never_interrupts_mock_live(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    await runtime.initialize()
    request = StartSessionRequest(
        source_language="ko", target_languages=["en"],
        engine="mock", translation_provider="none",
    )
    live = await runtime.start(request)
    assert live.recording_enabled is False
    assert runtime.archive.recordings(live.session_id) == []
    try:
        with pytest.raises(ValueError, match="real audio-input"):
            await runtime.start(request.model_copy(update={"record_audio": True}))
        assert runtime.state.session_id == live.session_id
        assert runtime.state.running is True
    finally:
        await runtime.stop()
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_disk_setup_error_never_blocks_live_asr(tmp_path, monkeypatch) -> None:
    """A full/unwritable disk must disable only recording, not the captions."""
    class FakeAudioEngine:
        accepts_audio = True
        sample_rate = 16000
        queue_depth = 0
        queue_capacity = 16
        failure = None
        running = True
        async def start(self, request):
            return None
        async def feed_audio(self, pcm):
            assert pcm
        async def end_audio(self):
            return None
        async def stop(self):
            return None

    async def disk_denied(self):
        raise OSError("simulated disk unavailable")

    monkeypatch.setattr(SessionRecordingWriter, "start", disk_denied)
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "bad-storage.db")))
    monkeypatch.setattr(runtime, "_build_engine", lambda _: FakeAudioEngine())
    try:
        state = await runtime.start(StartSessionRequest(
            record_audio=True, source_language="ko",
            target_languages=["en"], engine="mock",
            translation_provider="none",
        ))
        assert state.running is True
        assert state.recording_enabled is True
        assert "storage initialization failed" in (state.recording_error or "")
        await runtime.feed_audio(struct.pack("<f", 0.25) * 64)
        assert runtime.metrics.audio_frames_received == 1
        assert runtime.archive.recordings(state.session_id) == []
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_asr_provider_failure_does_not_discard_previously_received_audio(
    tmp_path, monkeypatch,
) -> None:
    class FailingAudioEngine:
        accepts_audio = True
        sample_rate = 16000
        queue_depth = 0
        queue_capacity = 16
        failure = None
        running = True
        async def start(self, request):
            return None
        async def feed_audio(self, pcm):
            raise RuntimeError("simulated ASR unavailable")
        async def end_audio(self):
            return None
        async def stop(self):
            return None

    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "provider-failed.db"),
        storage_reserve_mb=64,
    ))
    monkeypatch.setattr(runtime, "_build_engine", lambda _: FailingAudioEngine())
    try:
        state = await runtime.start(StartSessionRequest(
            record_audio=True, source_language="ko",
            target_languages=["en"], engine="mock",
            translation_provider="none",
        ))
        with pytest.raises(RuntimeError, match="simulated ASR"):
            await runtime.feed_audio(struct.pack("<f", 0.2) * 320)
        ended = await runtime.stop()
        assert ended.recording_error is None
        recordings = runtime.archive.recordings(state.session_id)
        assert len(recordings) == 1
        assert recordings[0]["duration_seconds"] == pytest.approx(320 / 16000)
    finally:
        await runtime.shutdown()



@pytest.mark.asyncio
async def test_fsync_error_discards_uncommitted_recording(tmp_path, monkeypatch) -> None:
    from langtextflow import recording_writer

    archive = _archive(tmp_path)
    writer = SessionRecordingWriter(
        archive, session_id="session-1", sample_rate=16000, reserve_bytes=0,
    )
    await writer.start()
    writer.offer(struct.pack("<f", 0.1) * 320)

    def failed_fsync(fd):
        raise OSError("simulated storage flush failure")

    monkeypatch.setattr(recording_writer.os, "fsync", failed_fsync)
    assert await writer.stop() is None
    assert "flush failed" in (writer.error or "")
    assert archive.recordings("session-1") == []
    assert list(archive.audio_root.glob("*.partial")) == []
    assert list(archive.audio_root.glob("*.wav")) == []


def test_restart_reclaims_only_old_abandoned_partial_wavs(tmp_path) -> None:
    import os
    import time

    archive = _archive(tmp_path)
    root = archive.audio_root
    old_partial = root / ("a" * 32 + ".wav.partial")
    recent_partial = root / ("b" * 32 + ".wav.partial")
    unknown_file = root / "operator-notes.wav.partial"
    complete = root / ("c" * 32 + ".wav")
    for path in (old_partial, recent_partial, unknown_file, complete):
        path.write_bytes(b"private test recording")
    old_time = time.time() - 30 * 3600
    os.utime(old_partial, (old_time, old_time))
    os.utime(unknown_file, (old_time, old_time))
    os.utime(complete, (old_time, old_time))

    # Startup must leave in-progress sessions and completed recordings intact.
    archive.initialize()
    assert not old_partial.exists()
    assert recent_partial.exists()
    assert unknown_file.exists()
    assert complete.exists()
    assert archive.cleanup_stale_partials() == 0
    with pytest.raises(ValueError):
        archive.cleanup_stale_partials(older_than_seconds=10)
