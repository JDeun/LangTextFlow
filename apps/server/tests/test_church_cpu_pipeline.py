"""Safety and provenance contracts for the experimental CPU church pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_church_cpu_pipeline import (
    MODEL_IDS,
    acceptable_output,
    percentile,
    validate_fixtures,
)


@pytest.mark.parametrize(
    ("source", "output", "valid"),
    [
        ("주님이 함께 하십니다.", "The Lord is with us.", True),
        ("주님이 함께 하십니다.", "", False),
        ("주님이 함께 하십니다.", "주님이 함께 하십니다.", False),
        ("text", '{"session_title":"worship","source_text":"hello"}', False),
        ("text", 'The context includes "source_text": "private"', False),
        ("text", '["unexpected","metadata"]', False),
    ],
)
def test_translation_never_accepts_metadata_or_untranslated_content(
    source: str, output: str, valid: bool,
) -> None:
    assert acceptable_output(source, output) is valid


def test_percentile_handles_empty_and_interpolation() -> None:
    assert percentile([], 0.95) is None
    assert percentile([10, 20], 0.95) == 19.5
    assert percentile([2, 3, 4], 0.5) == 3


def test_corpus_hash_and_language_direction_are_fixed() -> None:
    path = Path("benchmarks/church_translation_quality.v1.jsonl")
    rows, sha = validate_fixtures(path)
    assert len(rows) == 16
    assert all(r["source_language"] == "ko" for r in rows)
    assert all(r["target_language"] == "en" for r in rows)
    assert len(sha) == 64
    assert len({r["id"] for r in rows}) == len(rows)


def test_corpus_rejects_duplicate_and_missing_cases(tmp_path: Path) -> None:
    path = tmp_path / "fixture.jsonl"
    row = {
        "id": "bad", "source_language": "ko", "target_language": "en",
        "source_text": "안녕하세요", "reference_text": "Hello",
    }
    path.write_text("\n".join(json.dumps(row) for _ in range(20)), encoding="utf-8")
    with pytest.raises(ValueError, match="20 distinct"):
        validate_fixtures(path)


def test_model_ids_are_explicit_licensed_korean_to_english_candidates() -> None:
    assert MODEL_IDS == {
        "opus-small": "Helsinki-NLP/opus-mt-ko-en",
        "opus-big": "Helsinki-NLP/opus-mt-tc-big-ko-en",
    }
