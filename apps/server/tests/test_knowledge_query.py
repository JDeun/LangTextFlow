"""Tests for local keyword knowledge search."""

from pathlib import Path

from scripts.query_knowledge import search


def test_search_ranks_and_returns_source_path(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "asr.md").write_text("# ASR\\n한국어 자막 ASR ASR\\n", encoding="utf-8")
    (wiki / "mt.md").write_text("# MT\\n일본어 번역\\n", encoding="utf-8")
    results = search(tmp_path, "ASR", 5)
    assert len(results) == 1
    assert results[0][1] == Path("wiki/asr.md")
    assert results[0][0] >= 2


def test_unicode_query(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "ja.md").write_text("# 日本語\\n字幕 翻訳\\n", encoding="utf-8")
    assert search(tmp_path, "翻訳")[0][1] == Path("raw/ja.md")


def test_empty_query(tmp_path: Path) -> None:
    assert search(tmp_path, "  ") == []
