from __future__ import annotations

import array
import math
import sys
from datetime import UTC, datetime

from pydantic import BaseModel

MAX_ABS_PCM_SAMPLE = 4.0
MAX_PCM_FRAME_BYTES = 1024 * 1024


class RealtimeMetrics(BaseModel):
    audio_frames_received: int = 0
    audio_bytes_received: int = 0
    audio_duration_ms: float = 0.0
    audio_rms_dbfs: float | None = None
    voice_active: bool = False
    last_audio_enqueue_wait_ms: float | None = None
    audio_backpressure_events: int = 0
    asr_provider: str | None = None
    asr_running: bool = False
    asr_failure: str | None = None
    asr_failover_count: int = 0
    asr_last_failover_reason: str | None = None
    asr_last_failover_audio_ms: float | None = None
    asr_queue_depth: int = 0
    asr_queue_capacity: int = 0
    asr_queue_high_watermark: int = 0
    persistence_queue_depth: int = 0
    persistence_queue_capacity: int = 0
    postprocess_queue_depth: int = 0
    postprocess_queue_capacity: int = 0
    last_asr_lag_ms: float | None = None
    last_correction_latency_ms: float | None = None
    last_translation_latency_ms: float | None = None
    last_commit_latency_ms: float | None = None
    last_event_at: datetime | None = None


def decode_pcm_f32le(
    pcm_f32le: bytes,
    *,
    max_frame_bytes: int = MAX_PCM_FRAME_BYTES,
) -> array.array:
    """Validate and decode one little-endian float32 PCM frame.

    Browser audio should stay close to [-1, 1]. A wider absolute limit allows
    modest DSP overshoot while rejecting NaN/Inf and absurd amplitudes before
    they can poison RMS telemetry or downstream ASR buffers.
    """

    if not pcm_f32le or len(pcm_f32le) % 4:
        raise ValueError("audio frame must contain little-endian float32 PCM")
    if len(pcm_f32le) > max_frame_bytes:
        raise ValueError("audio frame is larger than the configured safety limit")

    samples = array.array("f")
    samples.frombytes(pcm_f32le)
    if sys.byteorder != "little":
        samples.byteswap()
    for sample in samples:
        if not math.isfinite(sample):
            raise ValueError("audio frame contains non-finite float32 samples")
        if abs(sample) > MAX_ABS_PCM_SAMPLE:
            raise ValueError("audio frame contains implausible float32 PCM amplitude")
    return samples


class EnergyVad:
    """Dependency-free activity detector used for monitoring, never audio dropping.

    This intentionally acts as a conservative signal meter rather than a semantic
    speech classifier. Keeping all PCM frames preserves ASR timing and avoids
    clipping quiet speech in reverberant rooms.
    """

    def __init__(
        self,
        threshold_dbfs: float = -45.0,
        hangover_frames: int = 3,
        *,
        max_frame_bytes: int = MAX_PCM_FRAME_BYTES,
    ) -> None:
        self.threshold_dbfs = threshold_dbfs
        self.hangover_frames = max(0, hangover_frames)
        self.max_frame_bytes = max(4, max_frame_bytes)
        self._hangover = 0

    def reset(self) -> None:
        self._hangover = 0

    def analyze(self, pcm_f32le: bytes) -> tuple[float, bool]:
        samples = decode_pcm_f32le(
            pcm_f32le,
            max_frame_bytes=self.max_frame_bytes,
        )
        if not samples:
            return -120.0, False

        mean_square = sum(sample * sample for sample in samples) / len(samples)
        rms = math.sqrt(mean_square)
        dbfs = 20.0 * math.log10(max(rms, 1e-6))
        active_now = dbfs >= self.threshold_dbfs
        if active_now:
            self._hangover = self.hangover_frames
            return dbfs, True
        if self._hangover > 0:
            self._hangover -= 1
            return dbfs, True
        return dbfs, False


def latency_ms(start: datetime, end: datetime | None = None) -> float:
    end = end or datetime.now(UTC)
    return max(0.0, round((end - start).total_seconds() * 1000.0, 1))
