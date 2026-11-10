"""Private telemetry ABI, populations and replay/final ordering; no models."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from langtextflow.asr.base import AsrEngineError
from langtextflow.config import Settings
from langtextflow.models import MAX_TARGET_LANGUAGES
from langtextflow.runtime import CaptionRuntime
from langtextflow.runtime_benchmark import RuntimeMetricsCollector
from langtextflow.rust_runtime import RustRuntimeBridge


def frame(sequence=1, final=False):
    def queue(waiting, active):
        return dict(queued=waiting, capacity=4, active=active, high=waiting + active,
                    admitted=waiting + active, succeeded=0, failed=0, expired=0,
                    cancelled=0, overflow=0, unavailable=0, wait_samples=active,
                    wait_p50_ms=3. if active else None, wait_p95_ms=3. if active else None)
    return {
        "type": "metrics", "session_id": "owned-session", "session_generation": 1,
        "sequence": sequence, "sampled_at": "2026-11-10T00:00:00Z", "final": final,
        "stop_error": None,
        "ingress": dict(accepted_frames=3, accepted_samples=192000, processed_samples=64000,
                        decoded_windows=1, empty_windows=0, queue_high_watermark=2,
                        queue_depth=0 if final else 2, queue_capacity=8,
                        backpressure_events=1, clipped_samples=0, rms_dbfs=-20.,
                        voice_active=not final, failure=None),
        "source": dict(active_window=not final, pending_corrections=0 if final else 2,
                       pending_targets=0 if final else 3),
        "correction": queue(0, 0) if final else queue(1, 1),
        "translation": queue(0, 0) if final else queue(2, 1),
        "history": None,
        "observer": dict(capacity_rejections=0, queue_evictions=0, send_timeouts=0,
                         snapshot_evictions=0, connected=0 if final else 1),
    }


def bridge():
    value = RustRuntimeBridge(None, Settings(), asyncio.Semaphore(1))
    value.session_id = "owned-session"
    return value


def accept(owner, value):
    return owner._accept_metrics(value, len(json.dumps(value).encode()) + 1)


def test_freshness_replay_and_unknown_are_not_idle_python_zero(monkeypatch, tmp_path):
    from langtextflow import rust_runtime

    clock = [1.]
    monkeypatch.setattr(rust_runtime.time, "monotonic", lambda: clock[0])
    owner = bridge()
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "unused.db")))
    runtime._rust_session = True
    runtime.engine = owner
    collector = RuntimeMetricsCollector()
    missing = runtime.metrics_snapshot()
    assert missing.rust_authority.status == "not_sampled"
    assert missing.asr_queue_depth is None and missing.postprocess_queue_depth is None
    collector.sample(missing)
    assert collector.postprocess_queue_peak is None and collector.persistence_queue_peak is None
    accept(owner, frame())
    measured = runtime.metrics_snapshot()
    assert measured.rust_authority.status == "fresh"
    assert measured.asr_queue_depth == 2 and measured.postprocess_queue_depth == 5
    assert measured.postprocess_queue_capacity == 10  # Two FIFOs plus their execution slots.
    assert measured.postprocess_segments_failed is None
    assert measured.postprocess_queue_lag_p95_ms is None  # Different lane populations.
    assert measured.persistence_queue_depth is None  # No native writer attached.
    collector.sample(measured)
    assert collector.postprocess_queue_peak == 5
    clock[0] = 3.
    accept(owner, frame())
    stale = runtime.metrics_snapshot()
    assert stale.rust_authority.status == "stale" and stale.rust_authority.age_ms == 2000
    assert stale.asr_queue_depth is None and stale.postprocess_queue_depth is None
    accept(owner, frame(2, final=True))
    final = runtime.metrics_snapshot()
    assert final.rust_authority.status == "final" and final.asr_queue_depth == 0
    runtime.engine = None
    assert runtime.metrics_snapshot().rust_authority.sample.final is True
    clock[0] = 5.
    assert runtime.metrics_snapshot().rust_authority.age_ms == 2000
    with pytest.raises(AsrEngineError):
        accept(owner, frame(3))  # No mutable publication after the terminal sample.


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra="owned-private-endpoint"),
    lambda value: value.update(sequence=True),
    lambda value: value.update(sequence=2**53),
    lambda value: value.update(sampled_at="not a clock"),
    lambda value: value["correction"].update(queued=17),
    lambda value: value["correction"].update(wait_samples=513),
    lambda value: value["correction"].update(wait_p95_ms=float("nan")),
    lambda value: value["correction"].update(admitted=0),
    lambda value: value["correction"].update(wait_samples=0),
    lambda value: value["ingress"].update(accepted_frames=True),
    lambda value: value["ingress"].update(failure="http://owned-private-endpoint"),
], ids=["extra", "bool", "counter-cap", "clock", "queue-bound", "ring-bound", "nan",
        "population", "unobserved-percentile", "bool-counter", "private-error"])
def test_strict_metrics_refuses_untrusted_shapes_and_values(change):
    value = frame()
    change(value)
    with pytest.raises(ValueError):
        accept(bridge(), value)


def test_session_generation_size_and_conflicting_replay_fences():
    owner = bridge()
    first = frame()
    accept(owner, first)
    for change in [{"session_id": "another-session"}, {"session_generation": 2}]:
        with pytest.raises(AsrEngineError):
            accept(owner, first | change)
    conflict = copy.deepcopy(first)
    conflict["ingress"]["accepted_frames"] += 1
    with pytest.raises(AsrEngineError):
        accept(owner, conflict)
    with pytest.raises(AsrEngineError):
        owner._accept_metrics(first, 4097)
    accept(owner, frame(2))
    accept(owner, first)  # Older sequence is ignored, never installed or refreshed.
    assert owner.metrics_view().sample.sequence == 2


def test_pending_target_population_accepts_all_retained_targets_but_stays_bounded():
    value = frame()
    maximum = 512 * MAX_TARGET_LANGUAGES
    value["source"]["pending_targets"] = maximum
    assert len(json.dumps(value).encode()) + 1 <= 4096
    assert accept(bridge(), value).source.pending_targets == maximum
    for invalid in (maximum + 1, -1, True):
        value["source"]["pending_targets"] = invalid
        with pytest.raises(ValueError):
            accept(bridge(), value)


def test_synthetic_source_keeps_pcm_unmeasured_in_metrics_and_collector(tmp_path):
    owner = bridge()
    value = frame()
    value["ingress"] = None
    value["source"].update(active_provider="mock", active_window=False)
    accept(owner, value)
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "unused.db")))
    runtime._rust_session, runtime.engine = True, owner
    sample = runtime.metrics_snapshot()
    assert sample.rust_authority.sample.ingress is None
    assert sample.asr_provider == "mock" and runtime.state.engine == "mock"
    assert sample.asr_queue_depth is sample.asr_queue_capacity is None
    assert sample.asr_queue_high_watermark is None
    collector = RuntimeMetricsCollector()
    collector.sample(sample)  # Null PCM must not break existing collectors.
    assert collector.postprocess_queue_peak == 5


async def reply(owner, values, kind="stopped"):
    reader = asyncio.StreamReader()
    reader.feed_data(b"".join((json.dumps(value) + "\n").encode() for value in values))
    reader.feed_eof()
    owner._process = SimpleNamespace(stdout=reader)
    return await owner._reply(kind)


async def test_final_sample_precedes_stopped_without_widening_ack_or_replaying_old_final():
    stopped = {"type": "stopped", "session_id": "owned-session", "session_generation": 1}
    owner = bridge()
    assert await reply(owner, [frame(2, final=True), stopped]) == stopped
    assert owner.metrics_view().status == "final"
    oversized_ack = stopped | {"padding": "x" * 1024}
    for values in ([stopped], [frame(), stopped], [frame(2, True), frame(2, True), stopped],
                   [frame(2, True), oversized_ack]):
        with pytest.raises(AsrEngineError):
            await reply(bridge(), values)
    owner = bridge()
    accept(owner, frame(3))
    with pytest.raises(AsrEngineError):
        await reply(owner, [frame(2, True), stopped])
    with pytest.raises(AsrEngineError):
        await reply(bridge(), [frame(2, True)], kind="ack")


async def test_final_reply_keeps_one_absolute_read_budget():
    owner = bridge()
    owner._process = SimpleNamespace(stdout=asyncio.StreamReader())
    with pytest.raises(TimeoutError):
        await owner._reply("stopped", .02)
