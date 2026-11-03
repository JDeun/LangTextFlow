"""Synthetic sample-clock evidence; never native ASR word-alignment acceptance."""

from __future__ import annotations

import asyncio
import struct
import wave

import pytest

from langtextflow import runtime as runtime_module
from langtextflow.asr.base import AsrEngineError
from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.recording_writer import SessionRecordingWriter
from langtextflow.runtime import CaptionRuntime


def frame(samples, quantized):
    assert len(samples) == len(quantized)
    return struct.pack(f"<{len(samples)}f", *samples), struct.pack(
        f"<{len(quantized)}h", *quantized
    )


class SampleClockAsr:
    accepts_audio = True
    sample_rate = 16000
    queue_depth = 0
    queue_capacity = 0
    failure = None

    def __init__(self, *, fail_on_call=None):
        self.running = False
        self.offered = []
        self.accepted = []
        self.samples = 0
        self.fail_on_call = fail_on_call

    async def start(self, request):
        self.request = request
        self.running = True

    async def feed_audio(self, pcm):
        self.offered.append(pcm)
        if len(self.offered) == self.fail_on_call:
            self.failure = "fixture ASR rejected this frame before admission"
            raise AsrEngineError(self.failure)
        start = self.samples
        self.samples += len(pcm) // 4
        self.accepted.append(pcm)
        text = f"synthetic PCM window {len(self.accepted)}"
        await self.publish(
            TranscriptEvent(
                segment_id=f"clock-{len(self.accepted):04d}",
                version=1,
                stage=CaptionStage.STABLE,
                text=text,
                original_text=text,
                source_language=self.request.source_language,
                start_ms=round(start * 1000 / self.sample_rate),
                end_ms=round(self.samples * 1000 / self.sample_rate),
            )
        )

    async def end_audio(self):
        pass

    async def stop(self):
        self.running = False


async def start_recording(tmp_path, monkeypatch, engine):
    runtime = CaptionRuntime(
        Settings(
            database_path=str(tmp_path / "clock.db"),
            storage_reserve_mb=64,
        )
    )
    monkeypatch.setattr(runtime, "_faster_whisper_engine", lambda **kwargs: engine)
    state = await runtime.start(
        StartSessionRequest(
            engine="faster-whisper",
            source_language="ko",
            target_languages=["en"],
            correction_provider="none",
            translation_provider="none",
            record_audio=True,
        )
    )
    assert state.running and runtime._recorder is not None
    return runtime, state.session_id


def recorded_pcm(runtime, session_id):
    recordings = runtime.archive.recordings(session_id)
    assert len(recordings) == 1
    metadata = recordings[0]
    path = runtime.archive.recording_path(session_id, metadata["recording_id"])
    with wave.open(str(path), "rb") as audio:
        return (
            metadata,
            audio.getnframes(),
            audio.getframerate(),
            audio.readframes(audio.getnframes()),
        )


def source_ranges(runtime, session_id):
    segments = runtime.history.segments(session_id)
    assert segments and all(segment.committed for segment in segments)
    return [(segment.start_ms, segment.end_ms) for segment in segments]


@pytest.mark.asyncio
async def test_varied_frames_silence_pause_and_rejected_pcm_keep_one_sample_clock(
    tmp_path,
    monkeypatch,
):
    engine = SampleClockAsr()
    runtime, session_id = await start_recording(tmp_path, monkeypatch, engine)
    frames = [
        frame([-0.5, 0.5] * 8, [-16384, 16384] * 8),
        frame([0.0] * 160, [0] * 160),
        frame([0.25] * 1024, [8192] * 1024),
        frame([-1.0, 1.0] * 240, [-32767, 32767] * 240),
    ]
    writer = runtime._recorder
    try:
        await runtime.feed_audio(frames[0][0])
        await asyncio.wait_for(writer._queue.join(), 1)
        for invalid in (struct.pack("<f", float("nan")), b"\x00\x00\x00"):
            with pytest.raises(ValueError):
                await runtime.feed_audio(invalid)
            assert engine.samples == writer.frames == 16
            assert runtime.metrics.audio_frames_received == 1
        await runtime.feed_audio(frames[1][0])  # Actual PCM silence is retained.
        await asyncio.sleep(0.03)  # A sender pause supplies no additional samples.
        for pcm, _ in frames[2:]:
            await runtime.feed_audio(pcm)
        stopped = await runtime.stop()
        metadata, samples, rate, saved = recorded_pcm(runtime, session_id)
        assert stopped.recording_error is None
        assert engine.accepted == [pcm for pcm, _ in frames]
        assert samples == writer.frames == engine.samples == 1680
        assert rate == 16000 and saved == b"".join(expected for _, expected in frames)
        assert metadata["duration_seconds"] == pytest.approx(1680 / 16000)
        assert runtime.metrics.audio_duration_ms == pytest.approx(105)
        assert runtime.metrics.audio_frames_received == 4
        assert source_ranges(runtime, session_id) == [(0, 1), (1, 11), (11, 75), (75, 105)]
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_recorder_overflow_discards_media_without_compressing_the_asr_clock(
    tmp_path,
    monkeypatch,
):
    drain = asyncio.Event()

    class PausedWriter(SessionRecordingWriter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, queue_chunks=1, **kwargs)

        async def _consume(self):
            await drain.wait()
            await super()._consume()

    monkeypatch.setattr(runtime_module, "SessionRecordingWriter", PausedWriter)
    engine = SampleClockAsr()
    runtime, session_id = await start_recording(tmp_path, monkeypatch, engine)
    frames = [
        struct.pack("<f", value) * count for value, count in ((0.25, 32), (0, 160), (-0.5, 48))
    ]
    try:
        for pcm in frames:
            await runtime.feed_audio(pcm)
        assert "overflow" in runtime.state.recording_error
        drain.set()
        stopped = await runtime.stop()
        assert "overflow" in stopped.recording_error
        assert engine.accepted == frames and engine.samples == 240
        assert source_ranges(runtime, session_id) == [(0, 2), (2, 12), (12, 15)]
        assert runtime.archive.recordings(session_id) == []
        assert not list(runtime.archive.audio_root.glob("*.wav"))
        assert not list(runtime.archive.audio_root.glob("*.partial"))
    finally:
        drain.set()
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_asr_rejected_tail_remains_in_wav_without_fabricated_transcript_coverage(
    tmp_path,
    monkeypatch,
):
    engine = SampleClockAsr(fail_on_call=2)
    runtime, session_id = await start_recording(tmp_path, monkeypatch, engine)
    prefix = frame([0.25] * 160, [8192] * 160)
    tail = frame([-0.5] * 320, [-16384] * 320)
    try:
        await runtime.feed_audio(prefix[0])
        with pytest.raises(AsrEngineError, match="before admission"):
            await runtime.feed_audio(tail[0])
        stopped = await runtime.stop()
        metadata, samples, rate, saved = recorded_pcm(runtime, session_id)
        assert stopped.recording_error is None
        assert engine.offered == [prefix[0], tail[0]] and engine.accepted == [prefix[0]]
        assert engine.samples == 160 and source_ranges(runtime, session_id) == [(0, 10)]
        assert samples == 480 and rate == 16000 and saved == prefix[1] + tail[1]
        assert metadata["duration_seconds"] == pytest.approx(0.03)
        assert runtime.metrics.audio_duration_ms == pytest.approx(30)
    finally:
        await runtime.shutdown()
