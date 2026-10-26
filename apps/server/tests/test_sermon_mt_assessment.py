"""M1 sermon MT assessment never conflates inference success and quality."""
from __future__ import annotations

import importlib.util
from pathlib import Path

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
    assert accepted["overall_status"] == "pass"
    rejected = assess(report, {"a": {"terminology_correct": False, "meaning_preserved": True}})
    assert rejected["directions"]["ko->en"]["quality_status"] == "fail"
    slow = assess(report, {"a": {"terminology_correct": True, "meaning_preserved": True}},
                  max_p95_ms=50)
    assert slow["directions"]["ko->en"]["runtime_status"] == "fail"
