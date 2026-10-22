"""Offline keyword search over approved Markdown knowledge records."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "docs" / "knowledge"


def search(root: Path, query: str, limit: int = 10) -> list[tuple[int, Path, str]]:
    terms = list(dict.fromkeys(re.findall(r"[^\\W_]+", query.casefold(), flags=re.UNICODE)))
    if not terms or limit < 1:
        return []
    results: list[tuple[int, Path, str]] = []
    for folder in ("wiki", "raw"):
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            body = path.read_text(encoding="utf-8")
            lines = body.splitlines()
            matches = [(sum(line.casefold().count(term) for term in terms), line) for line in lines]
            score = sum(count for count, _ in matches)
            if not score:
                continue
            best = max(matches, key=lambda item: item[0])[1].strip()
            results.append((score, path.relative_to(root), best[:200]))
    return sorted(results, key=lambda item: (-item[0], item[1].as_posix()))[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Keyword query, Korean/English/Japanese supported")
    parser.add_argument("--root", type=Path, default=KNOWLEDGE)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    for score, path, excerpt in search(args.root, args.query, args.limit):
        print(f"[{score}] {path}: {excerpt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
