"""Produce a reviewable wiki-update plan from immutable source metadata.

No network, hosted LLM, or automatic modification of wiki/ADR files.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "docs" / "knowledge"


def plan(root: Path, source: Path) -> str:
    raw = (root / "raw").resolve()
    record = source.resolve()
    if not record.is_relative_to(raw) or not record.is_file() or record.suffix != ".md":
        raise ValueError("Source must be an existing Markdown record under raw/")
    body = record.read_text(encoding="utf-8")
    fields = dict(re.findall(r"^- ([^:\n]+): (.+)$", body, flags=re.MULTILINE))
    required = ("Source URL", "Retrieved", "Revision/hash", "License/rights", "Evidence class")
    missing = [name for name in required if not fields.get(name)]
    if missing:
        raise ValueError(f"Missing source metadata: {', '.join(missing)}")
    title = body.splitlines()[0].removeprefix("# ").strip()
    if not title:
        raise ValueError("Missing source title")
    pages = sorted((root / "wiki").rglob("*.md"))
    terms = set(re.findall(r"[^\W_]+", title.casefold(), flags=re.UNICODE))
    candidates: list[tuple[int, Path]] = []
    for page in pages:
        text = page.read_text(encoding="utf-8").casefold()
        score = sum(text.count(term) for term in terms if len(term) > 2)
        if score:
            candidates.append((score, page.relative_to(root)))
    candidates.sort(key=lambda row: (-row[0], row[1].as_posix()))
    lines = [
        f"# Proposed wiki update: {title}",
        "",
        f"- Source record: `{record.relative_to(root)}`",
        f"- Source URL: {fields['Source URL']}",
        f"- Evidence class: {fields['Evidence class']}",
        f"- Retrieved: {fields['Retrieved']}",
        "- Status: **review required** (no claims automatically promoted)",
        "",
        "## Candidate wiki pages (keyword heuristic; verify manually)",
    ]
    lines.extend(f"- `{path}` (score {score})" for score, path in candidates[:5])
    if not candidates:
        lines.append("- No matching wiki page; consider a new page and index entry.")
    lines.extend(
        [
            "",
            "## Review checklist",
            "- [ ] Read the original source and verify rights, revision, and scope.",
            "- [ ] Compare claims against existing research and accepted ADRs.",
            "- [ ] Flag contradictory claims and distinguish reported from measured results.",
            "- [ ] Update wiki page(s), index and append-only log in a reviewed PR.",
            "- [ ] Run structural and Markdown link checks before merge.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--root", type=Path, default=KNOWLEDGE)
    args = parser.parse_args()
    try:
        print(plan(args.root, args.source))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
