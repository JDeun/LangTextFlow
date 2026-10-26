"""M1 sermon MT assessment never conflates inference success and quality."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "assess_sermon_mt.py"


def _assess():
    spec = importlib.util.spec_from_file_location("assess_sermon_mt", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.assess


def test_missing_human_review_never_passes_quality() -> None:
    report = {"records": [
        {"id": "a", "source_language": "ko", "target_language": "en",
         "error": None, "latency_ms": 100, "exact_match": True},
    ]}
    result = _assess()(report)
    assert result["directions"]["ko->en"]["runtime_status"] == "pass"
    assert result["directions"]["ko->en"]["quality_status"] == "needs_review"
    assert result["overall_status"] == "not_verified"


def test_manual_review_can_pass_quality_and_failed_terminology_cannot() -> None:
    report = {"records": [
        {"id": "a", "source_language": "ko", "target_language": "en",
         "error": None, "latency_ms": 100},
    ]}
    assess = _assess()
    accepted = assess(report, {"a": {"terminology_correct": True, "meaning_preserved": True}})
    assert accepted["directions"]["ko->en"]["quality_status"] == "pass"
    assert accepted["overall_status"] == "not_verified"
    assert len(accepted["missing_directions"]) == 5
    rejected = assess(report, {"a": {"terminology_correct": False, "meaning_preserved": True}})
    assert rejected["directions"]["ko->en"]["quality_status"] == "fail"
    slow = assess(report, {"a": {"terminology_correct": True, "meaning_preserved": True}},
                  max_p95_ms=50)
    assert slow["directions"]["ko->en"]["runtime_status"] == "fail"


def test_invalid_human_judgment_is_rejected() -> None:
    report = {"records": [
        {"id": "a", "source_language": "ko", "target_language": "en",
         "error": None, "latency_ms": 100},
    ]}
    with pytest.raises(ValueError, match="explicit boolean"):
        _assess()(report, {"a": {"terminology_correct": "yes", "meaning_preserved": True}})


def test_all_six_directions_are_required_for_overall_pass() -> None:
    languages = ("ko", "en", "ja")
    records = [
        {"id": f"{source}-{target}", "source_language": source,
         "target_language": target, "error": None, "latency_ms": 100}
        for source in languages for target in languages if source != target
    ]
    reviews = {
        row["id"]: {"terminology_correct": True, "meaning_preserved": True}
        for row in records
    }
    result = _assess()({"records": records}, reviews)
    assert result["overall_status"] == "pass"
    assert result["missing_directions"] == []
