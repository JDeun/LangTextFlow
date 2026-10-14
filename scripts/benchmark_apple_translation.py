"""Opt-in on-device Apple Translation benchmark: no provider downloads or cloud API.

Run only on your own Mac with Translation language assets already installed.
The benchmark is **text-only**, not Apple FaceTime/AirPods Live Translation or
iOS SpeechTranscriber. Synthetic references are unreviewed; no field claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
from pathlib import Path
from typing import Any

PAIRS = (
    ("en", "ko"), ("ja", "ko"), ("ko", "en"),
    ("ko", "ja"), ("en", "ja"), ("ja", "en"),
)
STATUS = frozenset({
    "completed", "invalid_request", "strategy_or_pair_not_installed",
    "strategy_unavailable", "translation_failed", "unavailable",
})


def input_cases(fixture: Path, strategy: str) -> list[dict[str, str]]:
    if strategy not in {"lowLatency", "highFidelity"}:
        raise ValueError("unsupported strategy")
    records: list[dict[str, str]] = []
    for line in fixture.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if data.get("reference_status") != "synthetic_unreviewed":
            raise ValueError("expected explicitly unreviewed synthetic fixture")
        for source, target in PAIRS:
            text = data[source]
            reference = data[target]
            if (not isinstance(text, str) or not isinstance(reference, str)
                    or not text.strip() or not reference.strip()
                    or len(text.encode()) > 4096):
                raise ValueError("missing or oversized source/reference")
            records.append({
                "id": f"{data['id']}-{source}-{target}",
                "source": source, "target": target, "text": text,
                "strategy": strategy,
                "reference": reference,
            })
    if not records or len(records) > 600:
        raise ValueError("bounded fixture must contain 1..600 test pairs")
    return records


def validate_responses(
    cases: list[dict[str, str]], lines: list[str],
) -> list[dict[str, Any]]:
    if len(lines) != len(cases):
        raise ValueError("missing or unexpected Apple response lines")
    results: list[dict[str, Any]] = []
    for case, line in zip(cases, lines, strict=True):
        result = json.loads(line)
        if not isinstance(result, dict):
            raise ValueError("Apple output must be an object")
        for field in ("id", "source", "target"):
            if result.get(field) != case[field]:
                raise ValueError(f"mismatched Apple translation {field}")
        if result.get("requestedStrategy") != case["strategy"]:
            raise ValueError("strategy response mismatch")
        status = result.get("status")
        if status not in STATUS:
            raise ValueError("invalid Apple translation status")
        output = result.get("translatedText")
        latency = result.get("latencyMs")
        if status == "completed":
            if not isinstance(output, str) or not output.strip():
                raise ValueError("claimed completed but missing translation")
            if output.strip() == case["text"].strip():
                raise ValueError("source copied as target caption")
            if not isinstance(latency, (int, float)) or not 0 <= latency <= 900_000:
                raise ValueError("invalid timing")
        elif output is not None:
            raise ValueError("failed translation must never show source as target")
        results.append({
            **case, "status": status, "translation": output,
            "translation_latency_ms": latency,
            "effective_strategy_verified": False,
        })
    return results


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    def p95(values: list[float]) -> float | None:
        if not values:
            return None
        values = sorted(values)
        index = int((len(values) - 1) * 0.95 + 0.999999)
        return round(values[index], 2)

    summary: dict[str, Any] = {}
    for source, target in PAIRS:
        rows = [r for r in results
                if r["source"] == source and r["target"] == target]
        timings = [r["translation_latency_ms"] for r in rows
                   if r["status"] == "completed"]
        summary[f"{source}->{target}"] = {
            "cases": len(rows),
            "completed": len(timings),
            "mt_only_p95_ms": p95(timings),
            "mt_only_median_ms": round(statistics.median(timings), 2)
            if timings else None,
            "status": "text_probe_only" if timings else "not_exercised",
            "human_quality_certified": False,
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", type=Path,
        default=Path("benchmarks/lecture_multilingual_ko_en_ja.v1.jsonl"),
    )
    parser.add_argument("--probe-bin", type=Path, required=True)
    parser.add_argument("--strategy", choices=("lowLatency", "highFidelity"),
                        required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-model-execution", action="store_true",
                        help="Explicitly consent to running local Apple translation")
    args = parser.parse_args()
    if not args.allow_model_execution:
        parser.error("local system model execution requires explicit opt-in")
    if platform.system() != "Darwin":
        parser.error("Apple Translation is only available on macOS")
    if not args.probe_bin.is_file():
        parser.error("compile AppleTranslationProbe.swift before execution")
    cases = input_cases(args.fixture, args.strategy)
    payload = "\n".join(json.dumps({k: v for k, v in item.items()
                                   if k != "reference"}, ensure_ascii=False)
                        for item in cases) + "\n"
    try:
        proc = subprocess.run(
            [str(args.probe_bin.resolve())], input=payload,
            text=True, capture_output=True, timeout=900, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("Apple translation probe exceeded the 15-minute limit") from exc
    if proc.returncode != 0:
        raise SystemExit("Apple translation probe failed; review platform support")
    # Do not print stderr: system failures could include local/private details.
    rows = validate_responses(cases, proc.stdout.splitlines())
    report = {
        "model": "apple.system.Translation.framework",
        "strategy_requested": args.strategy,
        "actual_model_identity_verified": False,
        "audio_input_exercised": False,
        "streaming_exercised": False,
        "field_ready": False,
        "fixture_review": "synthetic_unreviewed",
        "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
        "platform": platform.platform(),
        "directions": aggregate(rows),
        "translations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "strategy": args.strategy,
        "directions": report["directions"],
        "field_ready": False,
        "result_file": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
