from __future__ import annotations

import pytest

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
                source_language="en",
                aliases=["the grace"],
                translations={"ko": "은혜", "ja": "恵み"},
                presets=[ProductPreset.CHURCH],
            )
        ],
    )
    mappings = resolve_glossary(context, "ko", source_language="en")
    observed = measure_glossary_adherence(
        "We are saved by grace.", "우리는 은혜로 구원받았습니다.",
        mappings, "ko",
    )
    assert observed.relevant_terms == 1
    assert observed.matched_terms == 1
    assert observed.adherence == 1.0
    # Exact terms remain a lexical metric, not a semantic certificate.


def test_no_relevant_term_has_no_accuracy_claim() -> None:
    terms = resolve_glossary(_context(), "ja")
    report = measure_glossary_adherence("평범한 강연", "普通の講演", terms, "ja")
    assert report.relevant_terms == 0
    assert report.adherence is None
    assert report.missing == ()


@pytest.mark.parametrize(("source", "relevant"), [
    ("Her actions are misdeeds.", 0),
    ("An ACT follows.", 1),
    ("A deed follows.", 1),
    ("An ＡＣＴ follows.", 1),
])
def test_source_terms_and_aliases_use_the_live_provider_word_boundary(source, relevant):
    context = SessionContext(glossary=[GlossaryEntry(
        source_language="en", term="act", aliases=["deed"], translations={"ja": "行為"},
    )])
    mappings = resolve_glossary(context, "ja", source_language="en")
    report = measure_glossary_adherence(source, "行為", mappings, "ja")
    assert report.relevant_terms == report.matched_terms == relevant
    assert report.adherence == (1.0 if relevant else None)


def test_source_scope_keeps_homographs_distinct_and_unknown_source_unresolved():
    context = SessionContext(glossary=[
        GlossaryEntry(source_language="ko", term="grace", translations={"ja": "誤訳"}),
        GlossaryEntry(source_language="en", term="grace", translations={"ja": "恵み"}),
        GlossaryEntry(source_language="ja", term="grace", translations={"ko": "별도 뜻"}),
    ])
    assert [mapping.target for mapping in resolve_glossary(
        context, "ja", source_language=" EN ",
    )] == ["恵み"]
    assert [mapping.target for mapping in resolve_glossary(context, "ja")] == ["誤訳"]
    for source_language in (None, "", "und", "fr"):
        assert resolve_glossary(context, "ja", source_language=source_language) == ()


@pytest.mark.parametrize(("language", "source", "term", "alias", "target"), [
    ("ko", "칭이를 말합니다.", "칭의", "칭이", "justification"),
    ("ja", "しんこうを話します。", "信仰", "しんこう", "faith"),
])
def test_cjk_lexical_observation_remains_available(language, source, term, alias, target):
    context = SessionContext(glossary=[GlossaryEntry(
        source_language=language, term=term, aliases=[alias], translations={"en": target},
    )])
    report = measure_glossary_adherence(source, target, resolve_glossary(
        context, "en", source_language=language,
    ), "en")
    assert report.relevant_terms == report.matched_terms == 1


def test_disabled_or_different_target_is_not_counted() -> None:
    terms = resolve_glossary(_context(), "en")
    report = measure_glossary_adherence("무시와 칭의", "justification", terms, "ja")
    assert report.relevant_terms == 0
