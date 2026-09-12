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
