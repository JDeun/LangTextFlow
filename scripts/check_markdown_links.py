from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md"]
MARKDOWN_FILES.extend(sorted((ROOT / "docs").glob("*.md")))

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_LINK_RE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']", re.IGNORECASE)
SKIP_SCHEMES = {"http", "https", "mailto", "data"}


def _local_target(source: Path, raw: str) -> Path | None:
    value = raw.strip().strip("<>")
    if not value or value.startswith("#"):
        return None
    parsed = urlsplit(value)
    if parsed.scheme.casefold() in SKIP_SCHEMES or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    if path.startswith("/"):
        return ROOT / path.lstrip("/")
    return source.parent / path


def main() -> int:
    failures: list[str] = []
    for source in MARKDOWN_FILES:
        if not source.exists():
            if source.name == "CONTRIBUTING.md":
                continue
            failures.append(f"missing documentation file: {source.relative_to(ROOT)}")
            continue
        text = source.read_text(encoding="utf-8")
        targets = [*MARKDOWN_LINK_RE.findall(text), *HTML_LINK_RE.findall(text)]
        for raw in targets:
            target = _local_target(source, raw)
            if target is None:
                continue
            try:
                resolved = target.resolve(strict=False)
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                failures.append(
                    f"{source.relative_to(ROOT)}: link escapes repository: {raw}"
                )
                continue
            if not resolved.exists():
                failures.append(
                    f"{source.relative_to(ROOT)}: broken local link: {raw}"
                )

    if failures:
        print("Markdown link validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Markdown link validation passed ({len(MARKDOWN_FILES)} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
