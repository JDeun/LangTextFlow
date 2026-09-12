from array import array
from datetime import UTC, datetime, timedelta

import pytest

from langtextflow.asr.base import AsrEngine
from langtextflow.models import CaptionStage, SessionState, TranscriptEvent
from langtextflow.runtime import CaptionRuntime
from langtextflow.telemetry import EnergyVad, latency_ms


class DummyAudioEngine(AsrEngine):
    def __init__(self, publish) -> None:
        super().__init__(publish)
        self.frames: list[bytes] = []

    @property
    def running(self) -> bool:
        return True

    @property
    def accepts_audio(self) -> bool:
        return True

    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def queue_depth(self) -> int:
        return 3

    @property
    def queue_capacity(self) -> int:
        return 8

    async def start(self, request) -> None:
        del request

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        self.frames.append(pcm_f32le)

    async def end_audio(self) -> None:
        return

    async def stop(self) -> None:
        return


def pcm(value: float, samples: int = 1600) -> bytes:
    return array("f", [value] * samples).tobytes()


def test_energy_vad_detects_activity_and_hangover() -> None:
    vad = EnergyVad(threshold_dbfs=-40.0, hangover_frames=2)

    quiet_dbfs, quiet_active = vad.analyze(pcm(0.0001))
    loud_dbfs, loud_active = vad.analyze(pcm(0.1))
    _, hangover_one = vad.analyze(pcm(0.0001))
    _, hangover_two = vad.analyze(pcm(0.0001))
    _, expired = vad.analyze(pcm(0.0001))

    assert quiet_dbfs < -40.0
    assert quiet_active is False
    assert loud_dbfs > -40.0
    assert loud_active is True
    assert hangover_one is True
    assert hangover_two is True
    assert expired is False


def test_latency_ms_never_returns_negative_values() -> None:
    now = datetime.now(UTC)
    assert latency_ms(now, now + timedelta(milliseconds=123.4)) == 123.4
    assert latency_ms(now, now - timedelta(seconds=1)) == 0.0


@pytest.mark.asyncio
async def test_runtime_tracks_audio_activity_and_queue_depth() -> None:
    runtime = CaptionRuntime()
    engine = DummyAudioEngine(runtime.pipeline.ingest)
    runtime.engine = engine
    runtime.state = SessionState(
        running=True,
        engine="dummy",
        audio_required=True,
        audio_sample_rate=16000,
        started_at=datetime.now(UTC),
    )

    frame = pcm(0.1)
    await runtime.feed_audio(frame)
    metrics = runtime.metrics_snapshot()

    assert engine.frames == [frame]
    assert metrics.audio_frames_received == 1
    assert metrics.audio_bytes_received == len(frame)
    assert metrics.audio_duration_ms == pytest.approx(100.0)
    assert metrics.voice_active is True
    assert metrics.asr_queue_depth == 3
    assert metrics.asr_queue_capacity == 8
    assert metrics.asr_queue_high_watermark == 3
    assert metrics.last_audio_enqueue_wait_ms is not None


@pytest.mark.asyncio
async def test_runtime_tracks_stage_latency_by_segment() -> None:
    runtime = CaptionRuntime()
    base = datetime.now(UTC)
    runtime.state = SessionState(running=True, started_at=base)

    stable = TranscriptEvent(
        segment_id="seg-1",
        version=1,
        stage=CaptionStage.STABLE,
        source_language="ko",
        text="안녕하세요",
        start_ms=0,
        end_ms=1000,
        emitted_at=base + timedelta(milliseconds=1300),
    )
    corrected = stable.model_copy(
        update={
            "version": 2,
            "stage": CaptionStage.CORRECTED,
            "emitted_at": base + timedelta(milliseconds=1380),
        }
    )
    translated = corrected.model_copy(
        update={
            "version": 3,
            "stage": CaptionStage.TRANSLATED,
            "translations": {"en": "Hello"},
            "emitted_at": base + timedelta(milliseconds=1880),
        }
    )
    committed = translated.model_copy(
        update={
            "version": 4,
            "stage": CaptionStage.COMMITTED,
            "committed": True,
            "emitted_at": base + timedelta(milliseconds=1900),
        }
    )

    await runtime._publish(stable)
    await runtime._publish(corrected)
    await runtime._publish(translated)
    await runtime._publish(committed)
    metrics = runtime.metrics_snapshot()

    assert metrics.last_asr_lag_ms == 300.0
    assert metrics.last_correction_latency_ms == 80.0
    assert metrics.last_translation_latency_ms == 500.0
    assert metrics.last_commit_latency_ms == 600.0
