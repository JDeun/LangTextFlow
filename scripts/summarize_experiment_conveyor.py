"""Summarize workflow stage results separately from model quality evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

STAGES = ("catalog", "m2m_cpu", "church_quality", "church_live", "apple")
EXPECTED = {
    "metadata": {"catalog"},
    "cpu": {"catalog", "m2m_cpu"},
    "full": set(STAGES),
}
EVIDENCE = {
    "m2m_cpu": ("text-six-directions.json", "real-audio-six-directions.json"),
    "church_quality": ("church-translation-quality.json",),
    "church_live": ("live-interpretation-report.json",),
    "apple": ("apple-language-availability.json",),
}


def summarize(profile: str, needs: dict, files: set[str]) -> dict:
    if profile not in EXPECTED:
        raise ValueError("unsupported profile")
    rows = []
    problems = []
    for name in STAGES:
        required = name in EXPECTED[profile]
        status = needs.get(name, {}).get("result", "missing")
        if required and status != "success":
            problems.append(f"{name}: required result was {status}")
        if not required and status not in ("skipped", "missing"):
            problems.append(f"{name}: unexpected execution")
        if required and status == "success":
            for filename in EVIDENCE.get(name, ()):
                if filename not in files:
                    problems.append(f"{name}: missing evidence {filename}")
        rows.append({"stage": name, "required": required, "result": status})
    return {
        "profile": profile, "stages": rows, "problems": problems,
        "execution_complete": not problems,
        "translation_quality_certified": False,
        "glossary_effectiveness_certified": False,
        "apple_translation_executed": False,
        "sermon_field_ready": False,
    }


def markdown(report: dict) -> str:
    out = [
        "## Experiment conveyor",
        f"Profile: {report['profile']}",
        "| Stage | Required | Result |",
        "|---|---|---|",
    ]
    for row in report["stages"]:
        out.append(
            f"| {row['stage']} | {row['required']} | {row['result']} |"
        )
    out += [f"- {problem}" for problem in report["problems"]]
    out += [
        "**Translation quality / terminology:** not certified.",
        "**Apple system actual translation:** not executed by this workflow.",
        "**Long-session / 4GB and 8GB device readiness:** not certified.",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(EXPECTED), required=True)
    parser.add_argument("--needs-json", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    files = {
        p.name for p in args.artifact_dir.rglob("*") if p.is_file()
    } if args.artifact_dir.is_dir() else set()
    report = summarize(args.profile, json.loads(args.needs_json), files)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown(report), encoding="utf-8")
    if report["problems"]:
        raise SystemExit(markdown(report))


if __name__ == "__main__":
    main()
