from array import array
from datetime import UTC, datetime, timedelta

import pytest

from langtextflow.asr.base import AsrEngine
from langtextflow.models import CaptionStage, SessionState, TranscriptEvent
from langtextflow.runtime import CaptionRuntime
from langtextflow.telemetry import EnergyVad, latency_ms


class DummyAudioEngine(AsrEngine):
    def __init__(
        self,
        publish,
        *,
        failure: str | None = None,
        failover_count: int = 0,
        last_failover_reason: str | None = None,
    ) -> None:
        super().__init__(publish)
        self.frames: list[bytes] = []
        self._failure = failure
        self._failover_count = failover_count
        self._last_failover_reason = last_failover_reason

    @property
    def running(self) -> bool:
        return self._failure is None

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

    @property
    def failure(self) -> str | None:
        return self._failure

    @property
    def failover_count(self) -> int:
        return self._failover_count

    @property
    def last_failover_reason(self) -> str | None:
        return self._last_failover_reason

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
async def test_runtime_tracks_audio_activity_queue_and_provider_health() -> None:
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
    assert metrics.asr_provider == "dummy"
    assert metrics.asr_running is True
    assert metrics.asr_failure is None
    assert metrics.asr_failover_count == 0
    assert metrics.asr_last_failover_reason is None
    assert metrics.asr_queue_depth == 3
    assert metrics.asr_queue_capacity == 8
    assert metrics.asr_queue_high_watermark == 3
    assert metrics.last_audio_enqueue_wait_ms is not None


def test_runtime_reports_postprocessing_overload_and_stale_asr_counts() -> None:
    runtime = CaptionRuntime()
    runtime.pipeline.dropped_postprocess_segments = 4
    runtime.pipeline.failed_postprocess_segments = 2
    runtime.pipeline.duplicate_asr_events_dropped = 9
    runtime.stale_asr_events_dropped = 3
    runtime.pipeline._queue.put_nowait(None)

    first = runtime.metrics_snapshot()
    assert first.postprocess_queue_depth == 1
    assert first.postprocess_queue_high_watermark == 1
    assert first.postprocess_segments_dropped == 4
    assert first.postprocess_segments_failed == 2
    assert first.postprocess_duplicate_asr_events_dropped == 9
    assert first.stale_asr_events_dropped == 3

    runtime.pipeline._queue.get_nowait()
    runtime.pipeline._queue.task_done()
    runtime.pipeline.dropped_postprocess_segments += 1
    second = runtime.metrics_snapshot()
    assert second.postprocess_queue_depth == 0
    assert second.postprocess_queue_high_watermark == 1
    assert second.postprocess_segments_dropped == 5
    # Returned snapshots must not change after subsequent metrics updates.
    assert first.postprocess_segments_dropped == 4


def test_runtime_metrics_surface_provider_failure() -> None:
    runtime = CaptionRuntime()
    runtime.engine = DummyAudioEngine(runtime.pipeline.ingest, failure="GPU worker failed")
    runtime.state = SessionState(running=True, engine="faster-whisper")

    metrics = runtime.metrics_snapshot()

    assert metrics.asr_provider == "faster-whisper"
    assert metrics.asr_running is False
    assert metrics.asr_failure == "GPU worker failed"


def test_runtime_metrics_surface_recovered_failover() -> None:
    runtime = CaptionRuntime()
    runtime.engine = DummyAudioEngine(
        runtime.pipeline.ingest,
        failover_count=1,
        last_failover_reason="vibevoice: socket lost",
    )
    runtime.state = SessionState(running=True, engine="faster-whisper")

    metrics = runtime.metrics_snapshot()

    assert metrics.asr_running is True
    assert metrics.asr_failure is None
    assert metrics.asr_failover_count == 1
    assert metrics.asr_last_failover_reason == "vibevoice: socket lost"


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



@pytest.mark.asyncio
async def test_persistence_overflow_is_counted_without_blocking_caption_publication() -> None:
    import asyncio

    runtime = CaptionRuntime()
    runtime.state = SessionState(session_id="persistent-session", running=True)
    runtime._persistence_queue = asyncio.Queue(maxsize=1)
    existing = TranscriptEvent(
        segment_id="existing", version=1, stage=CaptionStage.STABLE,
        source_language="ko", text="원문", start_ms=0,
    )
    runtime._persistence_queue.put_nowait(("persistent-session", existing))
    # _publish only needs a worker-liveness sentinel. Do not create an
    # actual waiting worker that might consume the saturated queue here.
    runtime._persistence_task = asyncio.current_task()
    newest = existing.model_copy(update={"segment_id": "overflow", "text": "보존된 자막"})
    try:
        await runtime._publish(newest)
        snapshot = runtime.metrics_snapshot()
        assert runtime.store.snapshot()[-1].text == "보존된 자막"
        assert snapshot.persistence_events_dropped == 1
        assert snapshot.persistence_events_failed == 0
        assert snapshot.persistence_queue_high_watermark == 1
        assert runtime.state.persistence_error == "transcript persistence queue is full"
        assert runtime._persistence_queue.qsize() == 1
    finally:
        runtime._persistence_task = None


@pytest.mark.asyncio
async def test_persistence_worker_counts_failed_sqlite_write(monkeypatch) -> None:
    import asyncio

    runtime = CaptionRuntime()
    runtime.state = SessionState(session_id="write-failure-session", running=True)
    event = TranscriptEvent(
        segment_id="fail", version=1, stage=CaptionStage.STABLE,
        source_language="en", text="original", start_ms=0,
    )

    def fail_write(*_args) -> None:
        raise OSError("simulated database outage")

    monkeypatch.setattr(runtime.history, "upsert_segment", fail_write)
    await runtime._persistence_queue.put(("write-failure-session", event))
    worker = asyncio.create_task(runtime._persistence_worker())
    try:
        await asyncio.wait_for(runtime._persistence_queue.join(), timeout=2)
        assert runtime.metrics_snapshot().persistence_events_failed == 1
        assert runtime.state.persistence_error is not None
    finally:
        await runtime._persistence_queue.put(None)
        await asyncio.wait_for(worker, timeout=2)


def test_runtime_partial_event_metrics_do_not_accumulate_unbounded_ids() -> None:
    from langtextflow.config import Settings

    runtime = CaptionRuntime(Settings(max_segments=2))
    for n in range(100):
        runtime._observe_event(TranscriptEvent(
            segment_id=f"partial-{n}", version=1,
            stage=CaptionStage.PARTIAL, source_language="en",
            text="source", start_ms=0,
        ))
    assert runtime._stage_times == {}


def test_runtime_incomplete_segment_latency_memory_is_bounded() -> None:
    from langtextflow.config import Settings

    runtime = CaptionRuntime(Settings(max_segments=2))
    for n in range(100):
        runtime._observe_event(TranscriptEvent(
            segment_id=f"stable-{n}", version=1,
            stage=CaptionStage.STABLE, source_language="ko",
            text="원문", start_ms=0, end_ms=250,
        ))
        assert len(runtime._stage_times) <= 8

    assert "stable-0" not in runtime._stage_times
    assert "stable-99" in runtime._stage_times
    last = TranscriptEvent(
        segment_id="stable-99", version=2,
        stage=CaptionStage.COMMITTED, source_language="ko",
        text="원문", start_ms=0, end_ms=250, committed=True,
    )
    runtime._observe_event(last)
    assert "stable-99" not in runtime._stage_times
