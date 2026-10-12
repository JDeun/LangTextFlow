"""Independent comparison gate for Korean ASR CPU runner model reports.

Only compare measured candidate reports on identical pinned source audio,
references, duration, and dataset revision. Never silently rank mixed corpora.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        return {"status": "no-measured-models", "candidate_count": 0, "ranked": [],
                "field_ready": False}

    def source_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(row["id"]), str(row["pcm_sha256"]),
            str(row["reference"]), round(float(row["duration_seconds"]), 3),
        )

    expected: list[tuple[Any, ...]] | None = None
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for report in reports:
        if report.get("status") != "measured" or report.get("real_model") is not True:
            raise ValueError("refusing synthetic/unverified model evaluation")
        if report.get("dataset") != "google/fleurs" or report.get("source_language") != "ko":
            raise ValueError("not pinned Korean FLEURS")
        revision = report.get("revision")
        if revision != "fdd74f7cdf01fc7641ab3072b6a38d364b3667f6":
            raise ValueError("dataset revision mismatch")
        candidate = str(report.get("candidate", ""))
        if not candidate or candidate in seen:
            raise ValueError("duplicate/empty candidate")
        seen.add(candidate)
        samples = report.get("samples", [])
        if len(samples) != 3:
            raise ValueError("expected exact 3-clip Korean FLEURS corpus")
        keys = [source_key(row) for row in samples]
        if len({row[0] for row in keys}) != len(keys):
            raise ValueError("duplicate audio IDs")
        if expected is None:
            expected = keys
        elif keys != expected:
            raise ValueError("same audio, hashes, order, and reference text required")
        for row in samples:
            for key in ("cer", "wer"):
                value = row["quality"][key]
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"invalid {key} for {candidate}")
            if not str(row.get("hypothesis", "")).strip():
                raise ValueError("empty ASR hypothesis")
        ranked.append({
            "candidate": candidate,
            "mean_cer": round(sum(float(x["quality"]["cer"]) for x in samples) / 3, 4),
            "mean_wer": round(sum(float(x["quality"]["wer"]) for x in samples) / 3, 4),
            "mean_end_to_end_rtf": report.get("mean_end_to_end_rtf"),
            "rss_peak_mb": report.get("resources", {}).get("rss_peak_mb"),
            "streaming_kind": samples[0].get("streaming_kind"),
        })
    ranked.sort(key=lambda x: (x["mean_cer"], x["mean_wer"], x["candidate"]))
    return {
        "status": "comparable",
        "candidate_count": len(ranked),
        "real_fleurs_subset": [list(key[:2]) for key in expected or []],
        "ranked": ranked,
        "field_ready": False,
        "caveat": "Ranks describe only source Korean transcription on 3 FLEURS clips. "
                  "Cannot certify English translation or live church interpretation.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate([
        json.loads(path.read_text(encoding="utf-8")) for path in args.reports
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
