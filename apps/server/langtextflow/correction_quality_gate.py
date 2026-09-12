from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class QualityGatePolicy:
    minimums: dict[str, float]
    maximums: dict[str, float]


def load_policy(path: Path) -> QualityGatePolicy:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("quality gate policy must be a JSON object")
    minimums = payload.get("minimums", {})
    maximums = payload.get("maximums", {})
    if not isinstance(minimums, dict) or not isinstance(maximums, dict):
        raise ValueError("quality gate policy minimums/maximums must be objects")
    if not minimums and not maximums:
        raise ValueError("quality gate policy must define at least one threshold")

    def normalize(values: dict[str, object], label: str) -> dict[str, float]:
        output: dict[str, float] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{label} metric paths must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label}.{key} threshold must be numeric")
            output[key.strip()] = float(value)
        return output

    return QualityGatePolicy(
        minimums=normalize(minimums, "minimums"),
        maximums=normalize(maximums, "maximums"),
    )


def _resolve_metric(report: dict[str, Any], dotted_path: str) -> float | None:
    current: object = report
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    return float(current)


def evaluate_quality_gate(
    report: dict[str, Any],
    policy: QualityGatePolicy,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    for path, threshold in policy.minimums.items():
        actual = _resolve_metric(report, path)
        checks.append(
            {
                "metric": path,
                "rule": "minimum",
                "threshold": threshold,
                "actual": actual,
                "passed": actual is not None and actual >= threshold,
            }
        )
    for path, threshold in policy.maximums.items():
        actual = _resolve_metric(report, path)
        checks.append(
            {
                "metric": path,
                "rule": "maximum",
                "threshold": threshold,
                "actual": actual,
                "passed": actual is not None and actual <= threshold,
            }
        )

    report_status_ok = report.get("status") == "ok"
    provider_available = report.get("provider_available") is not False
    checks_passed = all(check["passed"] for check in checks)
    passed = report_status_ok and provider_available and checks_passed
    failures = [check for check in checks if not check["passed"]]
    if not report_status_ok:
        failures.append(
            {
                "metric": "status",
                "rule": "equals",
                "threshold": "ok",
                "actual": report.get("status"),
                "passed": False,
            }
        )
    if not provider_available:
        failures.append(
            {
                "metric": "provider_available",
                "rule": "equals",
                "threshold": True,
                "actual": False,
                "passed": False,
            }
        )

    return {
        "schema_version": 1,
        "passed": passed,
        "checks": checks,
        "failures": failures,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a LangTextFlow correction benchmark report against a JSON policy"
    )
    parser.add_argument("report", type=Path, help="correction benchmark JSON report")
    parser.add_argument("policy", type=Path, help="quality gate JSON policy")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("benchmark report must be a JSON object")
    result = evaluate_quality_gate(report, load_policy(args.policy))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
