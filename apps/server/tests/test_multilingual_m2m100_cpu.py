"""Six directed source/target combinations, not Korean-input-only translation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_multilingual_m2m100_cpu import (
    LANGUAGES,
    PAIRS,
    fleurs_parquet_uri,
    load_text_fixtures,
    safe_caption,
    targets_for_source,
    translate_to_targets,
)


class _FakeTranslator:
    def translate(self, text: str, source: str, target: str) -> str:
        return f"{target.upper()}: {text}"


def test_every_direct_pair_is_supported() -> None:
    assert set(PAIRS) == {
        ("en", "ko"), ("ja", "ko"), ("ko", "en"),
        ("ko", "ja"), ("en", "ja"), ("ja", "en"),
    }
    assert len(PAIRS) == 6
    for source in LANGUAGES:
        targets = targets_for_source(source)
        assert len(targets) == 2
        assert source not in targets
        assert len(set(targets)) == 2
    with pytest.raises(ValueError, match="source"):
        targets_for_source("fr")


@pytest.mark.parametrize("source", LANGUAGES)
def test_one_asr_text_is_translated_to_other_two_targets(source: str) -> None:
    observed = translate_to_targets(_FakeTranslator(), "Text", source)
    assert set(observed) == set(targets_for_source(source))
    assert all(entry["valid"] for entry in observed.values())


@pytest.mark.parametrize(
    "bad_output",
    [
        "", "  ", "Test input",
        '{"session_title":"private"}',
        '{"source_text":"private"}',
        '["translation"]',
    ],
)
def test_caption_never_passes_through_missing_or_private_metadata(
    bad_output: str,
) -> None:
    assert safe_caption("Test input", bad_output) is False


def test_research_corpus_has_three_language_fields_and_fixed_hash() -> None:
    path = Path("benchmarks/lecture_multilingual_ko_en_ja.v1.jsonl")
    rows, digest = load_text_fixtures(path)
    assert len(rows) == 12
    assert len(digest) == 64
    assert all(row.get("reference_status") == "synthetic_unreviewed" for row in rows)
    assert any("cannot" in row["en"] for row in rows)
    assert any("できません" in row["ja"] for row in rows)


def test_bad_bilingual_corpus_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(
        "\n".join(json.dumps({"id": str(i), "ko": "가", "en": "a"})
                  for i in range(9)),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ko, en and ja"):
        load_text_fixtures(path)


def test_same_pinned_dataset_source_for_ko_en_ja() -> None:
    for lang, config in (("ko", "ko_kr"), ("en", "en_us"), ("ja", "ja_jp")):
        uri = fleurs_parquet_uri(lang)
        assert "/"+config+"/test-00000-of-00001.parquet" in uri
        assert "google/fleurs@" in uri
    with pytest.raises(ValueError, match="unsupported"):
        fleurs_parquet_uri("fr")
