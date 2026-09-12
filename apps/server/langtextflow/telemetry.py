from __future__ import annotations

import array
import math
import sys
from datetime import UTC, datetime

from pydantic import BaseModel


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


class EnergyVad:
    """Dependency-free activity detector used for monitoring, never audio dropping.

    This intentionally acts as a conservative signal meter rather than a semantic
    speech classifier. Keeping all PCM frames preserves ASR timing and avoids
    clipping quiet speech in reverberant rooms.
    """

    def __init__(self, threshold_dbfs: float = -45.0, hangover_frames: int = 3) -> None:
        self.threshold_dbfs = threshold_dbfs
        self.hangover_frames = max(0, hangover_frames)
        self._hangover = 0

    def reset(self) -> None:
        self._hangover = 0

    def analyze(self, pcm_f32le: bytes) -> tuple[float, bool]:
        if not pcm_f32le or len(pcm_f32le) % 4:
            raise ValueError("audio frame must contain little-endian float32 PCM")
        samples = array.array("f")
        samples.frombytes(pcm_f32le)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return -120.0, False

        mean_square = sum(float(sample) * float(sample) for sample in samples) / len(samples)
        rms = math.sqrt(mean_square)
        dbfs = max(-120.0, 20.0 * math.log10(max(rms, 1e-6)))
        if dbfs >= self.threshold_dbfs:
            self._hangover = self.hangover_frames
            active = True
        elif self._hangover > 0:
            self._hangover -= 1
            active = True
        else:
            active = False
        return round(dbfs, 2), active


def now_utc() -> datetime:
    return datetime.now(UTC)


def latency_ms(start: datetime, end: datetime) -> float:
    return round(max(0.0, (end - start).total_seconds() * 1000.0), 1)
