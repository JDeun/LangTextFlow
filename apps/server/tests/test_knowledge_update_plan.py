"""Review plan generation regression tests."""

from pathlib import Path

import pytest

from scripts.plan_knowledge_update import plan


def fixture_root(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "raw"
    wiki = tmp_path / "wiki"
    raw.mkdir()
    wiki.mkdir()
    (wiki / "asr.md").write_text("# ASR\nASR speech models\n", encoding="utf-8")
    source = raw / "asr.md"
    source.write_text(
        "# ASR model\n- Source URL: https://example.org/asr\n"
        "- Retrieved: 2026-10-22\n- Revision/hash: v1\n"
        "- License/rights: Review required\n- Evidence class: external-reported\n",
        encoding="utf-8",
    )
    return tmp_path, source


def test_plan_contains_review_gate(tmp_path: Path) -> None:
    root, source = fixture_root(tmp_path)
    output = plan(root, source)
    assert "wiki/asr.md" in output
    assert "review required" in output
    assert "accepted ADRs" in output


def test_rejects_non_raw_source(tmp_path: Path) -> None:
    root, _ = fixture_root(tmp_path)
    with pytest.raises(ValueError):
        plan(root, root / "wiki" / "asr.md")


def test_missing_metadata(tmp_path: Path) -> None:
    root, source = fixture_root(tmp_path)
    source.write_text("# ASR\n", encoding="utf-8")
    with pytest.raises(ValueError):
        plan(root, source)
