"""Evaluate an actual translation run; never certify synthetic text as field-ready."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# These checks detect obvious regressions; neither claims semantic equivalence.
# Only evaluate negation in English, for which the patterns are explicit.
_EN_NEGATION = re.compile(
    r"\b(?:not|no|never|without|neither|nor|cannot|can't|don't|doesn't|"
    r"didn't|isn't|aren't|wasn't|weren't|won't|wouldn't|shouldn't|"
    r"couldn't|nothing|nobody|nowhere)\b", re.IGNORECASE,
)
_DIGITS = re.compile(r"\d+")


def semantic_sanity(reference: str, hypothesis: str, *, target: str) -> dict[str, Any]:
    """Flag digit or English-negation changes without rewriting translations.

    Numbers are compared as ordered decimal tokens, so scripture 3:16 and
    'chapter 3 verse 16' have the same numeric signature. Spelled-out
    numbers and cross-lingual negation scope still require human review.
    """
    expected = _DIGITS.findall(unicodedata.normalize("NFKC", reference))
    observed = _DIGITS.findall(unicodedata.normalize("NFKC", hypothesis))
    numeric_ok = expected == observed
    negation_checked = target.lower() == "en"
    reference_negative = bool(_EN_NEGATION.search(reference)) if negation_checked else None
    hypothesis_negative = bool(_EN_NEGATION.search(hypothesis)) if negation_checked else None
    polarity_ok = (
        reference_negative == hypothesis_negative if negation_checked else None
    )
    return {
        "expected_digits": expected,
        "observed_digits": observed,
        "digits_preserved": numeric_ok,
        "english_negation_checked": negation_checked,
        "reference_has_negation": reference_negative,
        "hypothesis_has_negation": hypothesis_negative,
        "negation_presence_preserved": polarity_ok,
        "semantic_equivalence_certified": False,
    }


def assess(report: dict[str, Any], fixture_bytes: bytes) -> dict[str, Any]:
    fixtures = [
        json.loads(line) for line in fixture_bytes.decode("utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not fixtures:
        raise ValueError("empty translation corpus")
    fixture_ids = [row["id"] for row in fixtures]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError("duplicate fixture ID; quality evidence is ambiguous")
    expected_sha = hashlib.sha256(fixture_bytes).hexdigest()
    if report.get("config", {}).get("fixture_sha256") != expected_sha:
        raise ValueError("fixture SHA-256 mismatch; reference corpus changed")

    reports = report.get("fixtures", [])
    if (
        len(reports) != len(fixtures)
        or {row.get("id") for row in reports} != {row["id"] for row in fixtures}
    ):
        raise ValueError("report IDs do not match translation corpus")
    report_ids = [row["id"] for row in reports]
    if len(set(report_ids)) != len(report_ids):
        raise ValueError("duplicate inference result ID; quality evidence is ambiguous")
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
        if hypothesis.startswith(("{", "[")):
            # Structural outputs must be unwrapped before publishing captions.
            # Reject valid JSON here independently of any particular provider.
            try:
                parsed_hypothesis = json.loads(hypothesis)
            except json.JSONDecodeError:
                parsed_hypothesis = None
            if isinstance(parsed_hypothesis, (dict, list)):
                reasons.append(f"{fixture['id']}: JSON object/array in caption")
        term_report = run.get("terminology") or {}
        if term_report.get("expected") != len(fixture.get("expected_terms", [])):
            raise ValueError(f"critical term accounting mismatch: {fixture['id']}")
        missing = list(term_report.get("missing") or [])
        if missing:
            reasons.append(f"{fixture['id']}: missing critical terms {missing}")
        target = str(fixture.get("target_language") or "en").lower()
        # A numeric discrepancy in the reference itself invalidates the
        # test case; do not grade a model against a corrupted fixture.
        source_digits = _DIGITS.findall(
            unicodedata.normalize("NFKC", fixture["source_text"])
        )
        reference_digits = _DIGITS.findall(
            unicodedata.normalize("NFKC", fixture["reference_text"])
        )
        if source_digits and source_digits != reference_digits:
            raise ValueError(f"fixture source/reference numbers differ: {fixture['id']}")
        sanity = semantic_sanity(fixture["reference_text"], hypothesis, target=target)
        if not sanity["digits_preserved"]:
            reasons.append(f"{fixture['id']}: numeric/verse reference mismatch")
        if sanity["negation_presence_preserved"] is False:
            reasons.append(f"{fixture['id']}: English negation presence mismatch")
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
            "semantic_sanity": sanity,
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
