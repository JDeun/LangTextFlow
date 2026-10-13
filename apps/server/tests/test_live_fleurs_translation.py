"""E2E runtime pilot must never count untranslated source as working English."""
from __future__ import annotations

import pytest

from scripts.evaluate_live_fleurs_translation import summarize_events


def event(id: str, stage: str, received: float, translated: str = "") -> dict:
    return {
        "segment_id": id, "stage": stage, "version": 1,
        "text": "믿음은 중요합니다.", "start_ms": 0, "end_ms": 2000,
        "received_ms": received,
        "translations": {"en": translated} if translated else {},
    }


def test_untranslated_caption_cannot_fake_successful_interpretation() -> None:
    result = summarize_events(
        [event("1", "stable", 2500), event("1", "committed", 2600)],
        2, 2.6,
    )
    assert result["committed_count"] == 1
    assert result["translated_committed_count"] == 0
    assert result["translation_coverage"] == 0
    assert result["translated_hypothesis"] == ""
    assert result["field_ready"] is False


def test_actual_translated_committed_event_has_end_to_end_lag() -> None:
    rows = [
        event("1", "stable", 2500),
        event("1", "translated", 4600, "Faith is important."),
        event("1", "committed", 4700, "Faith is important."),
    ]
    result = summarize_events(rows, 2, 4.7)
    assert result["translation_coverage"] == 1.0
    assert result["p95_lag_ms"]["stable"] == 500
    assert result["p95_lag_ms"]["translated"] == 2600
    assert result["processing_rtf"] == 2.35
    assert result["field_ready"] is False


def test_missing_or_invalid_audio_duration_is_not_a_pass() -> None:
    with pytest.raises(ValueError, match="positive"):
        summarize_events([], 0, 0.5)

def test_out_of_bounds_source_time_is_not_falsely_low_latency() -> None:
    rows = [
        event("invalid", "stable", 2200),
        event("invalid", "translated", 2500, "Faith is important."),
    ]
    for row in rows:
        row["end_ms"] = 15000
    summary = summarize_events(rows, 2, 2.5)
    assert summary["invalid_timestamp_count"] == 1
    assert summary["invalid_timestamp_segment_ids"] == ["invalid"]
    assert summary["p95_lag_ms"]["translated"] is None
    assert summary["field_ready"] is False
