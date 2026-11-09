from __future__ import annotations

import array
import math
import sys
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_ABS_PCM_SAMPLE = 4.0
MAX_PCM_FRAME_BYTES = 1024 * 1024

Counter = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
Milliseconds = Annotated[float, Field(ge=0, le=86_400_000, allow_inf_nan=False)]


class _StrictMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RustQueueMetrics(_StrictMetrics):
    queued: Annotated[int, Field(ge=0, le=17)]
    capacity: Annotated[int, Field(ge=1, le=16)]
    active: Annotated[int, Field(ge=0, le=1)]
    high: Annotated[int, Field(ge=0, le=17)]
    admitted: Counter
    succeeded: Counter
    failed: Counter
    expired: Counter
    cancelled: Counter
    overflow: Counter
    unavailable: Counter
    wait_samples: Annotated[int, Field(ge=0, le=512)]
    wait_p50_ms: Milliseconds | None
    wait_p95_ms: Milliseconds | None

    @model_validator(mode="after")
    def population(self):
        terminal = self.succeeded + self.failed + self.expired + self.cancelled
        if (self.queued + self.active > self.capacity + 1 or self.high > self.capacity + 1
                or self.high < self.queued or self.admitted != terminal + self.queued + self.active
                or self.wait_samples > self.admitted):
            raise ValueError("invalid job population")
        if self.wait_samples == 0:
            if self.wait_p50_ms is not None or self.wait_p95_ms is not None:
                raise ValueError("unobserved queue latency")
        elif (self.wait_p50_ms is None or self.wait_p95_ms is None
              or self.wait_p50_ms > self.wait_p95_ms):
            raise ValueError("invalid queue latency")
        return self


class RustIngressMetrics(_StrictMetrics):
    accepted_frames: Counter
    accepted_samples: Counter
    processed_samples: Counter
    decoded_windows: Counter
    empty_windows: Counter
    queue_high_watermark: Annotated[int, Field(ge=0, le=8)]
    queue_depth: Annotated[int, Field(ge=0, le=8)]
    queue_capacity: Literal[8]
    backpressure_events: Counter
    clipped_samples: Counter
    rms_dbfs: Annotated[float, Field(ge=-120, le=20, allow_inf_nan=False)] | None
    voice_active: bool
    failure: Annotated[str, Field(pattern=r"^[a-z_]{1,64}$")] | None


class RustSourceMetrics(_StrictMetrics):
    active_window: bool
    pending_corrections: Annotated[int, Field(ge=0, le=512)]
    pending_targets: Annotated[int, Field(ge=0, le=1024)]


class RustHistoryMetrics(_StrictMetrics):
    accepted: Counter
    persisted: Counter
    stale: Counter
    invalidated: Counter
    dropped: Counter
    write_errors: Counter
    complete: bool
    stopped: bool
    error: Annotated[str, Field(pattern=r"^[a-z_]{1,64}$")] | None
    queue_depth: Annotated[int, Field(ge=0, le=32)] | None
    queue_capacity: Annotated[int, Field(ge=1, le=32)]
    queue_high_watermark: Annotated[int, Field(ge=0, le=32)]


class RustObserverMetrics(_StrictMetrics):
    capacity_rejections: Counter
    queue_evictions: Counter
    send_timeouts: Counter
    snapshot_evictions: Counter
    connected: Annotated[int, Field(ge=0, le=8)]


class RustMetricsFrame(_StrictMetrics):
    type: Literal["metrics"]
    session_id: Annotated[str, Field(min_length=1, max_length=128)]
    session_generation: Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]
    sequence: Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)]
    sampled_at: Annotated[str, Field(max_length=40)]
    final: bool
    stop_error: Annotated[str, Field(pattern=r"^[a-z_]{1,64}$")] | None
    ingress: RustIngressMetrics
    source: RustSourceMetrics
    correction: RustQueueMetrics | None
    translation: RustQueueMetrics | None
    history: RustHistoryMetrics | None
    observer: RustObserverMetrics

    @field_validator("sampled_at")
    @classmethod
    def utc_time(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() != UTC.utcoffset(None):
            raise ValueError("metrics clock must be UTC")
        return value


class RustAuthorityMetrics(BaseModel):
    status: Literal["not_sampled", "fresh", "stale", "final", "unavailable"]
    age_ms: float | None = None
    sample: RustMetricsFrame | None = None
    queue_population: Literal["active_and_queued_correction_and_target_jobs"] = (
        "active_and_queued_correction_and_target_jobs"
    )
    wait_population: Literal["last_512_dequeued_jobs_per_lane"] = "last_512_dequeued_jobs_per_lane"
    ingress_population: Literal["accepted_pcm_frames"] = "accepted_pcm_frames"
    observer_population: Literal["private_rust_observer"] = "private_rust_observer"


class RealtimeMetrics(BaseModel):
    runtime_core: Literal["python", "rust"] = "python"
    rust_authority: RustAuthorityMetrics | None = None
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
    asr_queue_depth: int | None = 0
    asr_queue_capacity: int | None = 0
    asr_queue_high_watermark: int | None = 0
    persistence_queue_depth: int | None = 0
    persistence_queue_capacity: int | None = 0
    persistence_queue_high_watermark: int | None = 0
    persistence_events_dropped: int | None = 0
    persistence_events_failed: int | None = 0
    postprocess_queue_depth: int | None = 0
    postprocess_queue_capacity: int | None = 0
    postprocess_queue_lag_p50_ms: float | None = 0.0
    postprocess_queue_lag_p95_ms: float | None = 0.0
    websocket_clients: int = 0
    websocket_client_capacity: int = 0
    websocket_clients_rejected: int = 0
    websocket_overflow_evictions: int = 0
    websocket_send_failures: int = 0
    websocket_client_queue_high_watermark: int = 0
    postprocess_queue_high_watermark: int | None = 0
    postprocess_segments_dropped: int | None = 0
    postprocess_segments_failed: int | None = 0
    postprocess_segments_expired: int | None = 0
    postprocess_duplicate_asr_events_dropped: int | None = 0
    postprocess_revised_asr_events_rejected: int | None = 0
    stale_asr_events_dropped: int | None = 0
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
            pcm_f32le, max_frame_bytes=self.max_frame_bytes
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
