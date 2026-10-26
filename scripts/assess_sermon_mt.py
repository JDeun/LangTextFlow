"""Assess real sermon MT output by direction and explicit bilingual review.

Input: JSON from scripts/evaluate_sermon_mt.py, optionally enriched with
human_review entries keyed by sample id. Do not infer semantic correctness
from exact string matches.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def assess(
    report: dict, reviews: dict[str, dict] | None = None, *,
    max_p95_ms: float = 2000,
    min_terminology_accuracy: float = 0.9,
) -> dict:
    if max_p95_ms <= 0 or not 0 <= min_terminology_accuracy <= 1:
        raise ValueError("invalid assessment thresholds")
    reviews = reviews or {}
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in report["records"]:
        groups[f'{row["source_language"]}->{row["target_language"]}'].append(row)
    directions = {}
    for direction, rows in sorted(groups.items()):
        successes = [row for row in rows if row["error"] is None]
        durations = sorted(float(row["latency_ms"]) for row in successes)
        p95 = durations[max(0, math.ceil(len(durations) * 0.95) - 1)] if durations else None
        reviewed = [reviews[row["id"]] for row in rows if row["id"] in reviews]
        terminology = [review["terminology_correct"] for review in reviewed]
        semantic = [review["meaning_preserved"] for review in reviewed]
        terminology_accuracy = (
            statistics.mean(terminology) if terminology else None
        )
        coverage = len(reviewed) == len(rows)
        runtime_pass = len(successes) == len(rows) and p95 is not None and p95 <= max_p95_ms
        quality_pass = (
            coverage and bool(semantic) and all(semantic)
            and terminology_accuracy is not None
            and terminology_accuracy >= min_terminology_accuracy
        )
        directions[direction] = {
            "samples": len(rows),
            "inference_successes": len(successes),
            "p95_ms": p95,
            "runtime_status": "pass" if runtime_pass else "fail",
            "human_reviewed": len(reviewed),
            "terminology_accuracy": terminology_accuracy,
            "quality_status": "pass" if quality_pass else (
                "fail" if coverage else "needs_review"
            ),
        }
    return {
        "directions": directions,
        "overall_status": "pass" if directions and all(
            item["runtime_status"] == "pass" and item["quality_status"] == "pass"
            for item in directions.values()
        ) else "not_verified",
        "thresholds": {
            "max_p95_ms": max_p95_ms,
            "min_terminology_accuracy": min_terminology_accuracy,
        },
        "limitations": "Small curated corpus; manual bilingual review is mandatory.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-p95-ms", type=float, default=2000)
    parser.add_argument("--min-terminology-accuracy", type=float, default=0.9)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    reviews = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else None
    result = assess(report, reviews, max_p95_ms=args.max_p95_ms,
                    min_terminology_accuracy=args.min_terminology_accuracy)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
