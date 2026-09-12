from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path("apps/server/langtextflow")


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_true_keyword(node: ast.Call, name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg == name:
            return isinstance(keyword.value, ast.Constant) and keyword.value.value is True
    return False


def main() -> int:
    failures: list[str] = []
    calls = 0
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "snapshot_download":
                continue
            calls += 1
            if not _is_true_keyword(node, "local_files_only"):
                failures.append(
                    f"{path}:{node.lineno}: snapshot_download must set local_files_only=True"
                )

    if failures:
        print("Unsafe Hugging Face snapshot access detected:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"Hugging Face snapshot policy passed ({calls} call(s) checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())