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


def test_scripture_verse_number_drift_is_not_a_provisional_pass() -> None:
    report, content = sample()
    fixture = {
        "id": "ko-en-j316",
        "source_text": "요한복음 3장 16절을 읽습니다",
        "reference_text": "Read John chapter 3 verse 16",
        "target_language": "en",
        "expected_terms": ["John"],
    }
    content = (json.dumps(fixture, ensure_ascii=False) + "\n").encode()
    report["config"]["fixture_sha256"] = hashlib.sha256(content).hexdigest()
    report["fixtures"][0]["id"] = fixture["id"]
    run = report["fixtures"][0]["runs"][0]
    run["hypothesis"] = "Read John chapter 3 verse 17"
    evaluated = MODULE.assess(report, content)
    assert evaluated["status"] == "needs-improvement"
    assert evaluated["cases"][0]["semantic_sanity"]["expected_digits"] == ["3", "16"]
    assert "numeric/verse reference mismatch" in evaluated["reasons"][0]


def test_english_negation_regression_is_separately_reported() -> None:
    report, content = sample()
    fixture = {
        "id": "ko-en-negative",
        "source_text": "우리는 정죄함이 없습니다",
        "reference_text": "There is no condemnation",
        "target_language": "en",
        "expected_terms": ["condemnation"],
    }
    content = (json.dumps(fixture, ensure_ascii=False) + "\n").encode()
    report["config"]["fixture_sha256"] = hashlib.sha256(content).hexdigest()
    report["fixtures"][0]["id"] = fixture["id"]
    report["fixtures"][0]["runs"][0]["hypothesis"] = "There is condemnation"
    evaluated = MODULE.assess(report, content)
    assert evaluated["status"] == "needs-improvement"
    assert evaluated["cases"][0]["semantic_sanity"]["negation_presence_preserved"] is False
    assert any("English negation presence mismatch" in x for x in evaluated["reasons"])


def test_reference_mismatched_scripture_digits_fails_closed() -> None:
    report, content = sample()
    fixture = {
        "id": "ko-en-bad-fixture",
        "source_text": "요한복음 3장 16절",
        "reference_text": "John 3:17",
        "expected_terms": ["John"],
    }
    content = (json.dumps(fixture, ensure_ascii=False) + "\n").encode()
    report["config"]["fixture_sha256"] = hashlib.sha256(content).hexdigest()
    report["fixtures"][0]["id"] = fixture["id"]
    with pytest.raises(ValueError, match="source/reference numbers differ"):
        MODULE.assess(report, content)


def test_unchecked_non_english_negation_never_claims_semantic_pass() -> None:
    sanity = MODULE.semantic_sanity("罪はありません", "罪があります", target="ja")
    assert sanity["english_negation_checked"] is False
    assert sanity["negation_presence_preserved"] is None
    assert sanity["semantic_equivalence_certified"] is False


def test_equivalent_scripture_digit_punctuation_is_accepted() -> None:
    sanity = MODULE.semantic_sanity(
        "Read John chapter 3, verse 16.", "Read John 3:16.", target="en"
    )
    assert sanity["digits_preserved"] is True
    assert sanity["semantic_equivalence_certified"] is False


def test_quality_report_rejects_structured_json_caption_from_any_provider() -> None:
    report, content = sample()
    report["fixtures"][0]["runs"][0]["hypothesis"] = (
        '{"source_text":"요한복음","session_title":"service","translation":"John"}'
    )
    evaluated = MODULE.assess(report, content)
    assert evaluated["status"] == "needs-improvement"
    assert any("JSON object/array in caption" in r for r in evaluated["reasons"])


@pytest.mark.parametrize("duplicate_side", ["fixture", "report"])
def test_quality_report_rejects_duplicate_case_ids(duplicate_side: str) -> None:
    report, content = sample()
    fixture = json.loads(content.decode().strip())
    if duplicate_side == "fixture":
        content += (json.dumps(fixture, ensure_ascii=False) + "\n").encode()
        report["config"]["fixture_sha256"] = hashlib.sha256(content).hexdigest()
        report["fixtures"].append(report["fixtures"][0].copy())
        expected = "duplicate fixture ID"
    else:
        report["fixtures"].append(report["fixtures"][0].copy())
        expected = "report IDs|duplicate inference"
    with pytest.raises(ValueError, match=expected):
        MODULE.assess(report, content)
