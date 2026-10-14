from langtextflow.correction import DeterministicCorrector
from langtextflow.models import GlossaryEntry, ProductPreset, SessionContext


def test_church_preset_applies_only_explicit_safe_aliases() -> None:
    corrector = DeterministicCorrector()
    context = SessionContext(preset=ProductPreset.CHURCH)
    result = corrector.correct("오늘은  요한 보금 과 성녕에 대해 봅니다", context)
    assert result == "오늘은 요한복음 과 성령에 대해 봅니다"


def test_user_glossary_alias_replaces_with_canonical_term() -> None:
    corrector = DeterministicCorrector()
    context = SessionContext(
        glossary=[GlossaryEntry(term="VibeVoice", aliases=["바이브 보이스"])]
    )
    assert corrector.correct("바이브 보이스를 사용합니다", context) == "VibeVoice를 사용합니다"


def test_source_scoped_correction_does_not_rewrite_other_language_aliases() -> None:
    corrector = DeterministicCorrector()
    context = SessionContext(
        preset=ProductPreset.CHURCH,
        glossary=[
            GlossaryEntry(term="성령", aliases=["Holy Spirit"], source_language="ko"),
            GlossaryEntry(
                term="Holy Spirit", aliases=["Spirit of God"],
                source_language="en", presets=[ProductPreset.CHURCH],
            ),
            GlossaryEntry(
                term="Church-only", aliases=["conference only"],
                source_language="en", presets=[ProductPreset.CONFERENCE],
            ),
        ],
    )
    assert corrector.correct(
        "Holy Spirit and Spirit of God", context, source_language="en"
    ) == "Holy Spirit and Holy Spirit"
    assert corrector.correct(
        "Holy Spirit and Spirit of God", context, source_language="ja"
    ) == "Holy Spirit and Spirit of God"
    assert corrector.correct(
        "요한 보금과 성녕", context, source_language="en"
    ) == "요한 보금과 성녕"
    assert corrector.correct(
        "conference only", context, source_language="en"
    ) == "conference only"


def test_latin_alias_correction_respects_word_boundaries() -> None:
    context = SessionContext(glossary=[
        GlossaryEntry(term="Cross", source_language="en", aliases=["cross"])
    ])
    actual = DeterministicCorrector().correct(
        "We are crossing the road near the cross.", context,
        source_language="en",
    )
    assert actual == "We are crossing the road near the Cross."
