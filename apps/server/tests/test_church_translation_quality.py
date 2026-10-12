"""Translation-quality outcomes must not be confused with human certification."""
from __future__ import annotations

import hashlib
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "assess_church_translation.py"
SPEC = spec_from_file_location("assess_church_translation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sample() -> tuple[dict, bytes]:
    fixture = {
        "id": "ko-en-john", "source_text": "요한복음", "reference_text": "John",
        "expected_terms": ["John"], "reference_status": "synthetic_unreviewed",
    }
    content = (json.dumps(fixture, ensure_ascii=False) + "\n").encode()
    report = {
        "status": "ok",
        "config": {"fixture_sha256": hashlib.sha256(content).hexdigest()},
        "summary": {"terminology_accuracy": 1.0, "latency_p95_ms": 1200},
        "fixtures": [
            {"id": "ko-en-john", "runs": [
                {"status": "ok", "hypothesis": "John",
                 "terminology": {"expected": 1, "missing": []}}
            ]}
        ],
    }
    return report, content


def test_good_synthetic_translation_remains_provisional() -> None:
    report, content = sample()
    scored = MODULE.assess(report, content)
    assert scored["status"] == "provisional-pass"
    assert scored["human_semantic_review"] == "required"
    assert scored["completed_cases"] == 1


def test_missing_critical_term_is_a_quality_regression() -> None:
    report, content = sample()
    row = report["fixtures"][0]["runs"][0]
    row["hypothesis"] = "Books"
    row["terminology"]["missing"] = ["John"]
    assert MODULE.assess(report, content)["status"] == "needs-improvement"


@pytest.mark.parametrize("problem", ["digest", "id", "empty"])
def test_mismatched_or_empty_evaluation_refused(problem: str) -> None:
    report, content = sample()
    if problem == "digest":
        report["config"]["fixture_sha256"] = "wrong"
    elif problem == "id":
        report["fixtures"][0]["id"] = "different"
    else:
        content = b""
    with pytest.raises(ValueError):
        MODULE.assess(report, content)
