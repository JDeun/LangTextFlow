"""Compare two pinned real-speech FLEURS runs without mixing source clips.

Lower CER/WER and lower RTF are preferable, but benchmark changes must only
be interpreted if audio content, language, dataset and reference text match.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Fail rather than compare non-equivalent recordings."""
    for field in ("dataset", "revision", "split", "language"):
        if baseline.get(field) != candidate.get(field):
            raise ValueError(f"non-comparable {field}: {baseline.get(field)!r} vs {candidate.get(field)!r}")
    base_samples = baseline.get("samples", [])
    next_samples = candidate.get("samples", [])
    if not base_samples or not next_samples:
        raise ValueError("both runs must contain speech samples")
    def keyed(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        mapping = {str(row["id"]): row for row in samples}
        if len(mapping) != len(samples):
            raise ValueError("duplicate FLEURS sample IDs in one run")
        return mapping
    before = keyed(base_samples)
    after = keyed(next_samples)
    if before.keys() != after.keys():
        raise ValueError("non-comparable sample IDs")
    rows = []
    for identifier in before:
        b, a = before[identifier], after[identifier]
        for field in ("sha256_pcm16", "reference_characters"):
            if b.get(field) != a.get(field):
                raise ValueError(f"non-comparable sample {identifier}: {field} changed")
        if abs(float(b["audio_duration_seconds"]) - float(a["audio_duration_seconds"])) > 0.001:
            raise ValueError(f"non-comparable sample {identifier}: duration changed")
        for metric in ("cer", "wer", "throughput_rtf"):
            if not isinstance(b.get(metric), (int, float)) or not isinstance(a.get(metric), (int, float)):
                raise ValueError(f"missing numeric {metric} on {identifier}")
        rows.append({
            "id": identifier,
            "duration_seconds": b["audio_duration_seconds"],
            **{key: {"baseline": b[key], "candidate": a[key], "change": round(a[key] - b[key], 4)}
               for key in ("cer", "wer", "throughput_rtf")},
        })
    aggregate = {}
    for field in ("cer", "wer", "throughput_rtf"):
        name = f"mean_{field}"
        left = sum(row[field]["baseline"] for row in rows) / len(rows)
        right = sum(row[field]["candidate"] for row in rows) / len(rows)
        aggregate[name] = {
            "baseline": round(left, 4), "candidate": round(right, 4),
            "change": round(right - left, 4),
        }
    return {
        "status": "comparable",
        "dataset": baseline["dataset"],
        "revision": baseline["revision"],
        "language": baseline["language"],
        "samples": len(rows),
        "baseline_config": {"model": baseline.get("model"), "chunk_seconds": baseline.get("decode_chunk_seconds", 4)},
        "candidate_config": {"model": candidate.get("model"), "chunk_seconds": candidate.get("decode_chunk_seconds", 4)},
        "aggregate": aggregate,
        "per_sample": rows,
        "interpretation": "Negative CER/WER change means fewer errors; negative RTF change means faster processing. Pilot only.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare equivalent licensed FLEURS ASR runs")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = compare(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.candidate.read_text(encoding="utf-8")),
    )
    output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
