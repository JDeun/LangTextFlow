"""Deterministic structural lint for the Git-managed LangTextFlow knowledge wiki."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "docs" / "knowledge"
WIKI = KNOWLEDGE / "wiki"
INDEX = WIKI / "index.md"


def lint(root: Path = KNOWLEDGE) -> list[str]:
    wiki = root / "wiki"
    index = wiki / "index.md"
    errors: list[str] = []
    if not index.is_file():
        return [f"Missing wiki index: {index}"]
    index_text = index.read_text(encoding="utf-8")
    pages = sorted(wiki.rglob("*.md"))
    for page in pages:
        relative = page.relative_to(wiki).as_posix()
        body = page.read_text(encoding="utf-8")
        if not body.startswith("# "):
            errors.append(f"{relative}: missing H1 title")
        if page.name not in {"index.md", "log.md"}:
            if f"({relative})" not in index_text:
                errors.append(f"{relative}: not linked from wiki/index.md")
            for field in ("Status:", "Last reviewed:"):
                if field not in body:
                    errors.append(f"{relative}: missing {field}")
            if not re.search(r"\]\((?:\.\./|https?://)", body):
                errors.append(f"{relative}: missing evidence/source link")
        # Link integrity is checked separately by scripts/check_markdown_links.py.
    for match in re.finditer(r"\]\(([^)]+\.md)\)", index_text):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        resolved = (wiki / target).resolve()
        if not resolved.is_relative_to(root.resolve().parent) or not resolved.is_file():
            errors.append(f"wiki/index.md: broken local target {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=KNOWLEDGE)
    args = parser.parse_args()
    errors = lint(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Knowledge wiki structural lint: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
