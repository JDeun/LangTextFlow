"""Register a reviewed source as immutable metadata (no network or LLM calls)."""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "docs" / "knowledge" / "raw"


def register(
    *,
    raw_dir: Path,
    title: str,
    url: str,
    retrieved: str,
    revision: str,
    rights: str,
    evidence: str,
    issue: str,
) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Source URL must be a public HTTPS URL without credentials")
    date.fromisoformat(retrieved)
    if evidence not in {"external-reported", "reproduced", "hypothesis", "unverified"}:
        raise ValueError("Invalid evidence class")
    values = [title, url, retrieved, revision, rights, evidence, issue]
    if any(not v.strip() or "\\n" in v or "\\r" in v for v in values):
        raise ValueError("Fields must be nonempty single-line values")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:55]
    if not slug:
        raise ValueError("Title must contain an ASCII letter or digit")
    digest = hashlib.sha256(f"{url}\\n{revision}".encode()).hexdigest()[:12]
    target = raw_dir / f"{retrieved}-{slug}-{digest}.md"
    content = (
        f"# {title}\\n\\n"
        f"- Source URL: {url}\\n"
        f"- Retrieved: {retrieved}\\n"
        f"- Revision/hash: {revision}\\n"
        f"- License/rights: {rights}\\n"
        f"- Evidence class: {evidence}\\n"
        f"- Tracking issue: {issue}\\n\\n"
        "## Verification gaps\\n\\nNot yet independently verified. "
        "Link this record from a reviewed wiki synthesis PR.\\n"
    )
    raw_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive create ensures immutable provenance even under concurrent invocations.
    with target.open("x", encoding="utf-8") as file:
        file.write(content)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--retrieved", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--rights", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    args = parser.parse_args()
    try:
        path = register(
            raw_dir=args.raw_dir,
            title=args.title,
            url=args.url,
            retrieved=args.retrieved,
            revision=args.revision,
            rights=args.rights,
            evidence=args.evidence,
            issue=args.issue,
        )
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(f"Registered immutable source: {path}")
    print("Next: review provenance and update related wiki/index/log via PR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
