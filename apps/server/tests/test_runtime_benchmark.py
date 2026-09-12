from __future__ import annotations

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.runtime_benchmark import (
    RuntimeMetricsCollector,
    duplicate_metrics,
    failover_caption_gap_ms,
    latency_summary,
    max_timeline_gap_ms,
)
from langtextflow.telemetry import RealtimeMetrics


def event(segment_id: str, start_ms: int, end_ms: int, text: str) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id=segment_id,
        version=4,
        stage=CaptionStage.COMMITTED,
        source_language="ko",
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        committed=True,
    )


def test_runtime_benchmark_timeline_and_failover_gap() -> None:
    events = [
        event("a", 0, 1000, "첫 문장"),
        event("b", 1200, 2000, "둘째 문장"),
        event("c", 2400, 3000, "셋째 문장"),
    ]

    assert max_timeline_gap_ms(events) == 400
    assert failover_caption_gap_ms(events, 1500.0) == 0
    assert failover_caption_gap_ms(events, 2200.0) == 400
    assert failover_caption_gap_ms(events, None) is None


def test_runtime_benchmark_duplicate_metrics() -> None:
    events = [
        event("a", 0, 1000, "요한복음 3장"),
        event("b", 1000, 2000, " 요한복음   3장 "),
        event("c", 2000, 3000, "로마서 8장"),
    ]

    metrics = duplicate_metrics(events)

    assert metrics["exact_duplicate_count"] == 1
    assert metrics["exact_duplicate_rate"] == pytest.approx(1 / 3, abs=0.0001)


def test_runtime_metrics_collector_tracks_peaks_failover_and_latencies() -> None:
    collector = RuntimeMetricsCollector()
    first = RealtimeMetrics(
        audio_duration_ms=1200,
        asr_failover_count=0,
        postprocess_queue_depth=2,
        persistence_queue_depth=1,
        last_asr_lag_ms=300,
        last_correction_latency_ms=80,
    )
    second = RealtimeMetrics(
        audio_duration_ms=1500,
        asr_failover_count=1,
        postprocess_queue_depth=4,
        persistence_queue_depth=3,
        last_asr_lag_ms=420,
        last_correction_latency_ms=80,
        last_translation_latency_ms=600,
        last_commit_latency_ms=710,
    )

    collector.sample(first)
    collector.sample(second)

    assert collector.postprocess_queue_peak == 4
    assert collector.persistence_queue_peak == 3
    assert collector.failover_audio_ms == 1500
    assert collector.asr_latency_ms == [300.0, 420.0]
    assert collector.correction_latency_ms == [80.0]
    assert collector.translation_latency_ms == [600.0]
    assert collector.commit_latency_ms == [710.0]


def test_latency_summary_reports_percentiles() -> None:
    summary = latency_summary([100.0, 200.0, 300.0, 400.0])

    assert summary["count"] == 4
    assert summary["mean_ms"] == 250.0
    assert summary["p50_ms"] == 200.0
    assert summary["p95_ms"] == 400.0
    assert summary["max_ms"] == 400.0
