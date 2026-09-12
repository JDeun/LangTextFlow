from __future__ import annotations

import json
from pathlib import Path

import pytest

from langtextflow.correction_quality_gate import (
    QualityGatePolicy,
    evaluate_quality_gate,
    load_policy,
)


def sample_report() -> dict[str, object]:
    return {
        "status": "ok",
        "provider_available": True,
        "summary": {
            "exact_correct_rate": 0.97,
            "change_detection": {"precision": 0.99, "recall": 0.93},
            "harmful_change_rate": 0.0,
            "missed_correction_rate": 0.07,
            "wrong_change_rate": 0.02,
            "critical_token_accuracy": 1.0,
            "numeric_token_accuracy": 1.0,
            "latency_p95_ms": 1400.0,
        },
    }


def test_quality_gate_passes_when_all_thresholds_pass() -> None:
    policy = QualityGatePolicy(
        minimums={
            "summary.exact_correct_rate": 0.95,
            "summary.change_detection.precision": 0.98,
        },
        maximums={
            "summary.harmful_change_rate": 0.01,
            "summary.latency_p95_ms": 2500.0,
        },
    )

    result = evaluate_quality_gate(sample_report(), policy)

    assert result["passed"] is True
    assert result["failures"] == []
    assert all(check["passed"] for check in result["checks"])


def test_quality_gate_fails_missing_or_out_of_range_metrics() -> None:
    report = sample_report()
    summary = report["summary"]
    assert isinstance(summary, dict)
    summary["wrong_change_rate"] = 0.2
    policy = QualityGatePolicy(
        minimums={"summary.unknown_metric": 0.5},
        maximums={"summary.wrong_change_rate": 0.03},
    )

    result = evaluate_quality_gate(report, policy)

    assert result["passed"] is False
    assert len(result["failures"]) == 2
    assert result["failures"][0]["actual"] is None
    assert result["failures"][1]["actual"] == 0.2


def test_quality_gate_fails_unavailable_provider() -> None:
    report = sample_report()
    report["provider_available"] = False
    policy = QualityGatePolicy(
        minimums={"summary.exact_correct_rate": 0.95},
        maximums={},
    )

    result = evaluate_quality_gate(report, policy)

    assert result["passed"] is False
    assert any(failure["metric"] == "provider_available" for failure in result["failures"])


def test_load_policy_validates_thresholds(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "minimums": {"summary.exact_correct_rate": 0.95},
                "maximums": {"summary.harmful_change_rate": 0.01},
            }
        ),
        encoding="utf-8",
    )

    policy = load_policy(path)

    assert policy.minimums["summary.exact_correct_rate"] == 0.95
    assert policy.maximums["summary.harmful_change_rate"] == 0.01


def test_load_policy_rejects_empty_policy(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="at least one threshold"):
        load_policy(path)
