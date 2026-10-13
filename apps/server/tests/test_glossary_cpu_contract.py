"""Glossary must influence CPU model selection, not just post-hoc reports."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.compare_multilingual_m2m100_cpu import (
    LANGUAGES,
    load_text_fixtures,
    translate_to_targets,
)
from scripts.glossary_cpu_contract import (
    choose_candidate,
    load_glossary_snapshot,
    matched_terms,
    relevant_terms,
)

FIXTURE = Path("benchmarks/church_glossary_multilingual.v1.json")


class FakeModel:
    def __init__(self) -> None:
        self.rerank_calls: list[tuple[str, str]] = []

    def translate(self, text: str, source: str, target: str) -> str:
        return {
            "ko": "영은 신자를 인도합니다.",
            "en": "The spirit guides believers.",
            "ja": "霊は信者を導きます。",
        }[target]

    def translate_candidates(
        self, text: str, source: str, target: str, *, beam_size: int,
    ) -> list[str]:
        assert beam_size == 4
        self.rerank_calls.append((source, target))
        return [
            {
                "ko": "성령은 신자를 인도합니다.",
                "en": "The Holy Spirit guides believers.",
                "ja": "聖霊は信者を導きます。",
            }[target]
        ]


def test_glossary_export_shape_matches_source_scoped_operator_records() -> None:
    rows, digest = load_glossary_snapshot(FIXTURE)
    assert len(rows) == 9
    assert len(digest) == 64
    assert {row["source_language"] for row in rows} == set(LANGUAGES)
    assert all(row["presets"] == ["church"] for row in rows)


def test_legacy_or_unknown_language_import_is_fail_closed(tmp_path: Path) -> None:
    for bad_row in [
        {"term": "grace", "translations": {"ko": "은혜"}},
        {"term": "grace", "source_language": "xx", "translations": {"ko": "은혜"}},
    ]:
        path = tmp_path / "invalid.json"
        path.write_text(json.dumps({
            "schema_version": 1, "entries": [bad_row],
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="source_language"):
            load_glossary_snapshot(path)


def test_wrong_source_disabled_preset_and_latin_boundary() -> None:
    rows, _ = load_glossary_snapshot(FIXTURE)
    assert relevant_terms(rows, "We trust the Holy Spirit.", "en", "ko") == [
        {"source": "Holy Spirit", "target": "성령"}
    ]
    assert relevant_terms(rows, "We trust the Holy Spirit.", "ko", "en") == []
    assert relevant_terms(rows, "We trust the Holy Spirit.", "en", "ko", preset="lecture") == []
    assert relevant_terms(rows, "Not salvationism.", "en", "ko") == []
    disabled = {**rows[1], "enabled": False}
    assert relevant_terms([disabled], "Holy Spirit", "en", "ko") == []


def test_multilingual_synthetic_corpus_exercises_all_six_directions() -> None:
    rows, _ = load_glossary_snapshot(FIXTURE)
    cases, _ = load_text_fixtures(
        Path("benchmarks/lecture_multilingual_ko_en_ja.v1.jsonl")
    )
    for source in LANGUAGES:
        for target in LANGUAGES:
            if source == target:
                continue
            hits = sum(
                len(relevant_terms(rows, case[source], source, target))
                for case in cases
            )
            assert hits > 0, (source, target)


def test_glossary_actually_selects_an_mt_model_candidate() -> None:
    entries, _ = load_glossary_snapshot(FIXTURE)
    model = FakeModel()
    outputs = translate_to_targets(
        model, "성령께서 인도하십니다.", "ko", glossary_entries=entries
    )
    assert model.rerank_calls == [("ko", "en"), ("ko", "ja")]
    assert outputs["en"]["hypothesis"] == "The Holy Spirit guides believers."
    assert outputs["ja"]["hypothesis"] == "聖霊は信者を導きます。"
    for target in ("en", "ja"):
        record = outputs[target]["glossary"]
        assert record["applied"] is True
        assert record["baseline_matched"] == 0
        assert record["matched"] == 1
        assert record["glossary_guaranteed"] is False
        assert record["semantic_reviewed"] is False
        assert outputs[target]["baseline_latency_ms"] <= outputs[target]["latency_ms"]


def test_missing_model_term_is_reported_not_forcibly_substituted() -> None:
    terms = [{"source": "성령", "target": "Holy Spirit"}]
    out = choose_candidate(
        "The spirit guides believers.", ["The spirit guides believers."],
        "성령께서 인도하십니다.", terms, "en",
        safe=lambda source, output: bool(output) and source != output,
    )
    assert out["hypothesis"] == "The spirit guides believers."
    assert out["missing_targets"] == ["Holy Spirit"]
    assert out["adherence"] == 0
    assert out["strategy"] == "no_safe_improvement"


def test_number_negation_and_existing_term_protection() -> None:
    terms = [{"source": "성령", "target": "Holy Spirit"}]
    check = lambda source, output: bool(output) and source != output
    baseline = "The spirit did not leave us in 3 days."
    unsafe = ["The Holy Spirit left us in 4 days."]
    out = choose_candidate(baseline, unsafe, "성령", terms, "en", safe=check)
    assert out["hypothesis"] == baseline
    improved = ["The Holy Spirit did not leave us in 3 days."]
    assert choose_candidate(
        baseline, improved, "성령", terms, "en", safe=check
    )["applied"] is True

    already = [{"source": "성령", "target": "Holy Spirit"},
               {"source": "구원", "target": "salvation"}]
    baseline = "The Holy Spirit offers hope."
    option = ["Salvation is near, says the spirit."]
    out = choose_candidate(baseline, option, "성령과 구원", already, "en", safe=check)
    assert out["hypothesis"] == baseline


def test_no_relevant_glossary_does_not_fake_perfect_adherence() -> None:
    assert matched_terms("anything", []) == set()
    out = choose_candidate(
        "Translated text.", [], "Source text", [], "en",
        safe=lambda source, output: True,
    )
    assert out["adherence"] is None
    assert out["strategy"] == "not_applicable"
