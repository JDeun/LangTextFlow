"""Regression checks for curated M1 sermon translation evaluation anchors."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "validate_sermon_mt_eval.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_sermon_mt_eval", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sermon_eval_covers_all_six_directions() -> None:
    report = _load().validate()
    assert report["samples"] >= 9
    assert len(report["pairs"]) == 6
    assert "negation" in report["categories"]


def test_sermon_eval_rejects_duplicate_identifiers(tmp_path: Path) -> None:
    corpus = _load().CORPUS.read_text(encoding="utf-8").splitlines()
    duplicate = tmp_path / "duplicates.jsonl"
    duplicate.write_text("\n".join([corpus[0], corpus[0]]), encoding="utf-8")
    with pytest.raises(AssertionError):
        _load().validate(duplicate)
