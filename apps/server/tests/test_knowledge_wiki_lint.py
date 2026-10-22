"""Regression tests for knowledge wiki structure checks."""

from pathlib import Path

from scripts.check_knowledge_wiki import lint


def scaffold(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# Index\n\n[Page](page.md)\n", encoding="utf-8")
    (wiki / "page.md").write_text(
        "# Page\n\n- Status: seed\n- Last reviewed: 2026-10-22\n"
        "- [Source](../raw/README.md)\n",
        encoding="utf-8",
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "README.md").write_text("# Raw\n", encoding="utf-8")
    return tmp_path


def test_valid(tmp_path: Path) -> None:
    assert lint(scaffold(tmp_path)) == []


def test_orphan_and_missing_evidence(tmp_path: Path) -> None:
    root = scaffold(tmp_path)
    (root / "wiki" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    errors = lint(root)
    assert any("not linked" in e for e in errors)
    assert any("missing evidence" in e for e in errors)


def test_broken_index_link(tmp_path: Path) -> None:
    root = scaffold(tmp_path)
    (root / "wiki" / "index.md").write_text("# Index\n[Missing](gone.md)\n", encoding="utf-8")
    assert any("broken local target" in e for e in lint(root))
