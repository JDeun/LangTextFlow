from __future__ import annotations

from langtextflow.glossary_adherence import (
    measure_glossary_adherence,
    resolve_glossary,
)
from langtextflow.models import GlossaryEntry, ProductPreset, SessionContext


def _context() -> SessionContext:
    return SessionContext(
        preset=ProductPreset.CHURCH,
        glossary=[
            GlossaryEntry(
                term="칭의", aliases=["칭이"],
                translations={"en": "justification", "ja": "義認"},
                presets=[ProductPreset.CHURCH],
            ),
            GlossaryEntry(
                term="십자가",
                translations={"en": "cross", "ja": "十字架"},
                presets=[ProductPreset.CHURCH],
            ),
            GlossaryEntry(
                term="무시", translations={"en": "ignore", "ja": "無視"},
                enabled=False,
            ),
            GlossaryEntry(
                term="컨퍼런스", translations={"en": "conference"},
                presets=[ProductPreset.CONFERENCE],
            ),
        ],
    )


def test_resolved_language_only_includes_active_scoped_terms() -> None:
    context = _context()
    en = resolve_glossary(context, "EN")
    ja = resolve_glossary(context, "ja")
    assert [item.target for item in en] == ["justification", "cross"]
    assert [item.target for item in ja] == ["義認", "十字架"]
    assert all(item.language == "ja" for item in ja)


def test_english_exact_terms_do_not_match_substrings() -> None:
    terms = resolve_glossary(_context(), "en")
    observed = measure_glossary_adherence(
        "칭의와 십자가",
        "The crossing is unjustification, not the translated terms.",
        terms,
        "en",
    )
    assert observed.relevant_terms == 2
    assert observed.matched_terms == 0
    assert {miss.expected for miss in observed.missing} == {"cross", "justification"}


def test_japanese_measures_observed_terms_without_rewriting() -> None:
    terms = resolve_glossary(_context(), "ja")
    hypothesis = "義認と信仰について話します。"
    observed = measure_glossary_adherence("칭이와 십자가", hypothesis, terms, "ja")
    assert observed.relevant_terms == 2
    assert observed.matched_terms == 1
    assert observed.missing[0].source == "십자가"
    assert hypothesis == "義認と信仰について話します。"
    assert observed.evaluation_kind == (
        "exact_lexical_observation_not_semantic_certification"
    )


def test_english_missionary_source_to_korean_glossary_observation() -> None:
    context = SessionContext(
        preset=ProductPreset.CHURCH,
        glossary=[
            GlossaryEntry(
                term="grace",
                aliases=["the grace"],
                translations={"ko": "은혜", "ja": "恵み"},
                presets=[ProductPreset.CHURCH],
            )
        ],
    )
    mappings = resolve_glossary(context, "ko")
    observed = measure_glossary_adherence(
        "We are saved by grace.", "우리는 은혜로 구원받았습니다.",
        mappings, "ko",
    )
    assert observed.relevant_terms == 1
    assert observed.matched_terms == 1
    assert observed.adherence == 1.0
    # This only tests the lexical metric. Runtime source-language matching
    # requires explicit source-language metadata migration in issue #130.


def test_no_relevant_term_has_no_accuracy_claim() -> None:
    terms = resolve_glossary(_context(), "ja")
    report = measure_glossary_adherence("평범한 강연", "普通の講演", terms, "ja")
    assert report.relevant_terms == 0
    assert report.adherence is None
    assert report.missing == ()


def test_disabled_or_different_target_is_not_counted() -> None:
    terms = resolve_glossary(_context(), "en")
    report = measure_glossary_adherence("무시와 칭의", "justification", terms, "ja")
    assert report.relevant_terms == 0
