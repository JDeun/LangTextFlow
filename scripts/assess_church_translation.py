"""Evaluate an actual translation run; never certify synthetic text as field-ready."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def assess(report: dict[str, Any], fixture_bytes: bytes) -> dict[str, Any]:
    fixtures = [
        json.loads(line) for line in fixture_bytes.decode("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not fixtures:
        raise ValueError("empty translation corpus")
    expected_sha = hashlib.sha256(fixture_bytes).hexdigest()
    if report.get("config", {}).get("fixture_sha256") != expected_sha:
        raise ValueError("fixture SHA-256 mismatch; reference corpus changed")

    reports = report.get("fixtures", [])
    if (
        len(reports) != len(fixtures)
        or {row.get("id") for row in reports} != {row["id"] for row in fixtures}
    ):
        raise ValueError("report IDs do not match translation corpus")
    by_id = {row["id"]: row for row in reports}

    try:
        from sacrebleu.metrics import CHRF
        chrf = CHRF(word_order=2)
    except ImportError:
        chrf = None

    reasons: list[str] = []
    items: list[dict[str, Any]] = []
    chrf_scores: list[float] = []
    for fixture in fixtures:
        run_rows = by_id[fixture["id"]].get("runs", [])
        if len(run_rows) != 1 or run_rows[0].get("status") != "ok":
            reasons.append(f"{fixture['id']}: missing successful inference")
            continue
        run = run_rows[0]
        hypothesis = str(run.get("hypothesis") or "").strip()
        if not hypothesis or hypothesis == fixture["source_text"].strip():
            reasons.append(f"{fixture['id']}: empty/untranslated output")
        term_report = run.get("terminology") or {}
        if term_report.get("expected") != len(fixture.get("expected_terms", [])):
            raise ValueError(f"critical term accounting mismatch: {fixture['id']}")
        missing = list(term_report.get("missing") or [])
        if missing:
            reasons.append(f"{fixture['id']}: missing critical terms {missing}")
        score = (
            round(chrf.sentence_score(hypothesis, [fixture["reference_text"]]).score, 3)
            if chrf else None
        )
        if score is not None:
            chrf_scores.append(score)
        items.append({
            "id": fixture["id"],
            "category": fixture.get("category"),
            "hypothesis": hypothesis,
            "reference": fixture["reference_text"],
            "chrf_plus_plus": score,
            "latency_ms": run.get("latency_ms"),
            "missing_critical_terms": missing,
        })

    return {
        "status": "provisional-pass" if not reasons else "needs-improvement",
        "human_semantic_review": "required",
        "reference_status": "synthetic_unreviewed",
        "fixture_sha256": expected_sha,
        "total_cases": len(fixtures),
        "completed_cases": len(items),
        "mean_sentence_chrf_plus_plus": (
            round(sum(chrf_scores) / len(chrf_scores), 3) if chrf_scores else None
        ),
        "terminology_accuracy": report.get("summary", {}).get("terminology_accuracy"),
        "latency_p50_ms": report.get("summary", {}).get("latency_p50_ms"),
        "latency_p95_ms": report.get("summary", {}).get("latency_p95_ms"),
        "reasons": reasons,
        "cases": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assess(
        json.loads(args.report.read_text(encoding="utf-8")), args.fixtures.read_bytes()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {key: value for key, value in result.items() if key != "cases"},
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
