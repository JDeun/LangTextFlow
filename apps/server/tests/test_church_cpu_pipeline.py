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


def test_ct2_marian_uses_target_sentencepiece_not_source_token_ids() -> None:
    """OPUS sepvoc target pieces cannot be decoded through source token IDs."""
    from scripts.compare_church_cpu_pipeline import CTranslateMarian

    class TargetPieceDecoder:
        def decode_pieces(self, pieces):
            assert pieces == ["▁The", "▁Lord", "▁lives", "."]
            return "The Lord lives."

    class SeparateVocabTokenizer:
        all_special_tokens = ["</s>", "<pad>", "<unk>"]
        spm_target = TargetPieceDecoder()

        def encode(self, text):
            assert text == "주님이 살아계십니다."
            return [1, 2]

        def convert_ids_to_tokens(self, tokens):
            assert tokens == [1, 2]
            return ["▁주님이", "</s>"]

        def convert_tokens_to_ids(self, tokens):
            raise AssertionError("Must not map target pieces through source vocabulary")

    class FakeTranslator:
        def translate_batch(self, sources, **kwargs):
            assert sources == [["▁주님이", "</s>"]]
            assert kwargs["beam_size"] == 1
            return [type("Output", (), {"hypotheses": [
                ["▁The", "▁Lord", "▁lives", ".", "</s>"]
            ]})()]

    translator = object.__new__(CTranslateMarian)
    translator.tokenizer = SeparateVocabTokenizer()
    translator.engine = FakeTranslator()
    assert translator.translate("주님이 살아계십니다.") == "The Lord lives."


def test_ct2_marian_fails_closed_without_target_decoder() -> None:
    from scripts.compare_church_cpu_pipeline import CTranslateMarian

    class IncompleteTokenizer:
        all_special_tokens = ["</s>"]
        def encode(self, text):
            return [1]
        def convert_ids_to_tokens(self, tokens):
            return ["▁text", "</s>"]

    class FakeTranslator:
        def translate_batch(self, sources, **kwargs):
            return [type("Output", (), {"hypotheses": [["▁text", "</s>"]]})()]

    translator = object.__new__(CTranslateMarian)
    translator.tokenizer = IncompleteTokenizer()
    translator.engine = FakeTranslator()
    with pytest.raises(RuntimeError, match="target SentencePiece decoder"):
        translator.translate("임의 문장")
