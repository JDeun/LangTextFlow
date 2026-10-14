"""Structural/fail-closed tests: no Apple device, download or network needed."""
from __future__ import annotations

import json

import pytest

from scripts.benchmark_apple_translation import (
    PAIRS,
    aggregate,
    input_cases,
    validate_responses,
)


def _fixture(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps({
        "id": "sermon-01",
        "ko": "주님은 우리를 사랑하십니다.",
        "en": "The Lord loves us.",
        "ja": "主は私たちを愛しています。",
        "reference_status": "synthetic_unreviewed",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_one_fixture_generates_six_explicit_directions(tmp_path):
    cases = input_cases(_fixture(tmp_path), "lowLatency")
    assert len(cases) == 6
    assert {(c["source"], c["target"]) for c in cases} == set(PAIRS)
    assert all(c["source"] != c["target"] for c in cases)
    assert all(c["strategy"] == "lowLatency" for c in cases)


def test_unreviewed_fixture_required(tmp_path):
    p = _fixture(tmp_path)
    p.write_text(p.read_text().replace(
        "synthetic_unreviewed", "human_approved"
    ), encoding="utf-8")
    with pytest.raises(ValueError, match="unreviewed"):
        input_cases(p, "highFidelity")


def test_valid_but_unqualified_native_response(tmp_path):
    case = input_cases(_fixture(tmp_path), "highFidelity")[0]
    response = {
        "id": case["id"], "source": case["source"],
        "target": case["target"], "requestedStrategy": "highFidelity",
        "status": "completed", "translatedText": "번역된 문장",
        "latencyMs": 13.5, "effectiveStrategyVerified": False,
    }
    record = validate_responses([case], [json.dumps(response)])[0]
    assert record["translation"] == "번역된 문장"
    assert not record["effective_strategy_verified"]
    result = aggregate([record])
    assert result[f"{case['source']}->{case['target']}"]["completed"] == 1
    assert not result[f"{case['source']}->{case['target']}"]["human_quality_certified"]


@pytest.mark.parametrize("mutation,match", [
    ({"status": "completed", "translatedText": None}, "missing translation"),
    ({"status": "completed", "translatedText": "SOURCE"}, "source copied"),
    ({"status": "completed", "latencyMs": -1}, "invalid timing"),
    ({"status": "unsupported"}, "invalid Apple translation status"),
    ({"source": "de"}, "mismatched"),
    ({"requestedStrategy": "highFidelity"}, "strategy response mismatch"),
    ({"status": "translation_failed", "translatedText": "injected"}, "must never"),
])
def test_fail_closed_responses(tmp_path, mutation, match):
    case = input_cases(_fixture(tmp_path), "lowLatency")[0]
    case["text"] = "SOURCE"
    response = {
        "id": case["id"], "source": case["source"],
        "target": case["target"], "requestedStrategy": "lowLatency",
        "status": "completed", "translatedText": "Translated output",
        "latencyMs": 50.0, "effectiveStrategyVerified": False,
    }
    response.update(mutation)
    with pytest.raises(ValueError, match=match):
        validate_responses([case], [json.dumps(response)])


def test_missing_response_fails_instead_of_implicitly_falling_back(tmp_path):
    cases = input_cases(_fixture(tmp_path), "lowLatency")
    with pytest.raises(ValueError, match="missing"):
        validate_responses(cases, [])


def test_all_not_installed_stays_unqualified(tmp_path):
    cases = input_cases(_fixture(tmp_path), "lowLatency")
    lines = [json.dumps({
        "id": c["id"], "source": c["source"], "target": c["target"],
        "requestedStrategy": c["strategy"], "status": "strategy_or_pair_not_installed",
        "translatedText": None, "latencyMs": None,
        "effectiveStrategyVerified": False,
    }) for c in cases]
    report = aggregate(validate_responses(cases, lines))
    assert set(report) == {"en->ko", "ja->ko", "ko->en", "ko->ja", "en->ja", "ja->en"}
    assert all(r["status"] == "not_exercised" for r in report.values())
