"""Tests for immutable research source metadata registration."""

from pathlib import Path

import pytest

from scripts.ingest_knowledge_source import register


def sample(tmp_path: Path) -> dict:
    return {
        "raw_dir": tmp_path / "raw",
        "title": "Example Model Card",
        "url": "https://example.org/models/example",
        "retrieved": "2026-10-22",
        "revision": "v1",
        "rights": "Review required",
        "evidence": "external-reported",
        "issue": "#123",
    }


def test_register_is_immutable(tmp_path: Path) -> None:
    fields = sample(tmp_path)
    target = register(**fields)
    assert target.exists()
    assert "external-reported" in target.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        register(**fields)


def test_reject_non_https(tmp_path: Path) -> None:
    fields = sample(tmp_path)
    fields["url"] = "file:///etc/passwd"
    with pytest.raises(ValueError):
        register(**fields)


def test_reject_invalid_evidence(tmp_path: Path) -> None:
    fields = sample(tmp_path)
    fields["evidence"] = "verified-by-magic"
    with pytest.raises(ValueError):
        register(**fields)


def test_reject_newline_injection(tmp_path: Path) -> None:
    fields = sample(tmp_path)
    fields["title"] = "Bad\\n# Injected"
    with pytest.raises(ValueError):
        register(**fields)
