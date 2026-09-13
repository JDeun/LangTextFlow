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
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}

FRONTEND_FORBIDDEN = {
    "dangerouslySetInnerHTML": re.compile(r"\bdangerouslySetInnerHTML\b"),
    "innerHTML assignment": re.compile(r"\.innerHTML\s*="),
    "outerHTML assignment": re.compile(r"\.outerHTML\s*="),
    "insertAdjacentHTML": re.compile(r"\.insertAdjacentHTML\s*\("),
    "eval": re.compile(r"\beval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\s*\("),
    "document.write": re.compile(r"\bdocument\.write\s*\("),
}

BACKEND_FORBIDDEN = {
    "eval": re.compile(r"\beval\s*\("),
    "exec": re.compile(r"\bexec\s*\("),
    "os.system": re.compile(r"\bos\.system\s*\("),
    "shell=True": re.compile(r"\bshell\s*=\s*True\b"),
    "pickle load": re.compile(r"\bpickle\.(?:load|loads)\s*\("),
    "tempfile.mktemp": re.compile(r"\btempfile\.mktemp\s*\("),
}

FORBIDDEN_EXACT_PATHS = {
    ".env",
    ".coverage",
    "coverage.xml",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pyc",
}
FORBIDDEN_PATH_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "htmlcov",
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


def forbidden_tracked_path(relative: str) -> str | None:
    path = Path(relative)
    if relative in FORBIDDEN_EXACT_PATHS:
        return "runtime/local artifact"
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return "environment file"
    if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        return f"forbidden {path.suffix} artifact"
    if any(part in FORBIDDEN_PATH_PARTS for part in path.parts):
        return "generated/cache directory"
    return None


def main() -> int:
    findings: list[str] = []
    self_path = Path(__file__).resolve()
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        path_finding = forbidden_tracked_path(relative)
        if path_finding is not None:
            findings.append(f"{relative}: tracked {path_finding}")

        if path.resolve() == self_path:
            continue
        text = read_text(path)
        if text is None:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: possible {name}")
        if relative.startswith("apps/web/src/"):
            for name, pattern in FRONTEND_FORBIDDEN.items():
                if pattern.search(text):
                    findings.append(f"{relative}: forbidden frontend primitive {name}")
        if relative.startswith("apps/server/langtextflow/"):
            for name, pattern in BACKEND_FORBIDDEN.items():
                if pattern.search(text):
                    findings.append(f"{relative}: forbidden backend primitive {name}")

    if findings:
        print("Repository hygiene scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Repository hygiene scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
