from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_SCAN_BYTES = 2 * 1024 * 1024

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}

FRONTEND_FORBIDDEN = {
    "dangerouslySetInnerHTML": re.compile(r"\bdangerouslySetInnerHTML\b"),
    "eval": re.compile(r"\beval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\s*\("),
    "document.write": re.compile(r"\bdocument\.write\s*\("),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for raw in result.stdout.split(b"\x00"):
        if raw:
            paths.append(ROOT / raw.decode("utf-8", errors="strict"))
    return paths


def read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    findings: list[str] = []
    self_path = Path(__file__).resolve()
    for path in tracked_files():
        if path.resolve() == self_path:
            continue
        text = read_text(path)
        if text is None:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: possible {name}")
        if relative.startswith("apps/web/src/"):
            for name, pattern in FRONTEND_FORBIDDEN.items():
                if pattern.search(text):
                    findings.append(f"{relative}: forbidden frontend primitive {name}")

    if findings:
        print("Repository hygiene scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Repository hygiene scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
