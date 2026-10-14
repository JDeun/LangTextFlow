"""Multi-target glossary persistence, seeded mappings and translator contracts."""
from __future__ import annotations

import pytest

from langtextflow import main
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import GlossaryEntry, ProductPreset, SessionContext, StartSessionRequest
from langtextflow.presets import CHURCH_GLOSSARY
from langtextflow.translation.ollama import OllamaTranslator
from langtextflow.translation.openai_compatible import OpenAICompatibleTranslator


@pytest.mark.parametrize(
    ("term", "english", "japanese"),
    [
        ("요한복음", "Gospel of John", "ヨハネによる福音書"),
        ("로마서", "Romans", "ローマの信徒への手紙"),
        ("성령", "Holy Spirit", "聖霊"),
        ("칭의", "justification", "義認"),
        ("성화", "sanctification", "聖化"),
        ("속죄", "atonement", "贖罪"),
        ("십자가", "cross", "十字架"),
        ("복음", "gospel", "福音"),
    ],
)
def test_church_default_has_en_ja(term: str, english: str, japanese: str) -> None:
    entry = next(item for item in CHURCH_GLOSSARY if item.term == term)
    assert entry.translations == {"en": english, "ja": japanese}
    assert entry.presets == [ProductPreset.CHURCH]


def test_glossary_update_retains_ids_and_both_languages(tmp_path) -> None:
    repo = GlossaryRepository(str(tmp_path / "glossary.db"))
    repo.initialize()
    row = repo.create(
        GlossaryEntry(
            term="칭의", translations={"en": "justification", "ja": "義認"},
            presets=[ProductPreset.CHURCH], boost=2,
        )
    )
    updated = repo.update(
        row.id,
        GlossaryEntry(
            term="칭의", aliases=["칭이"],
            translations={"en": "justification", "ja": "義認", "fr": "justification"},
            presets=[ProductPreset.CHURCH, ProductPreset.LECTURE], boost=1.5,
        ),
    )
    assert updated is not None and updated.id == row.id
    assert updated.created_at == row.created_at
    assert repo.get(row.id).translations["ja"] == "義認"
    assert set(repo.get(row.id).translations) == {"en", "ja", "fr"}


def test_church_seed_is_non_destructive_for_user_edited_entries(tmp_path) -> None:
    repo = GlossaryRepository(str(tmp_path / "glossary.db"))
    repo.initialize()
    repo.seed(CHURCH_GLOSSARY)
    row = next(item for item in repo.list() if item.term == "복음")
    repo.update(
        row.id, GlossaryEntry(
            term="복음", translations={"en": "the gospel", "ja": "福音書"},
            presets=[ProductPreset.CHURCH],
        )
    )
    repo.seed(CHURCH_GLOSSARY)
    again = repo.get(row.id)
    assert again.translations == {"en": "the gospel", "ja": "福音書"}


def test_saved_en_ja_glossary_is_session_snapshot(monkeypatch, tmp_path) -> None:
    repo = GlossaryRepository(str(tmp_path / "glossary.db"))
    repo.initialize()
    saved = repo.create(
        GlossaryEntry(
            term="칭의", aliases=["칭이"],
            translations={"en": "justification", "ja": "義認"},
            presets=[ProductPreset.CHURCH],
        )
    )
    monkeypatch.setattr(main, "glossary_repository", repo)
    start = StartSessionRequest(context=SessionContext(preset=ProductPreset.CHURCH))
    request = main._with_saved_glossary(start)
    assert request.context.asr_hotwords(source_language="ko") == ["칭의", "칭이"]
    assert request.context.glossary[0].translations["ja"] == "義認"
    repo.delete(saved.id)
    assert request.context.glossary[0].translations["ja"] == "義認"


@pytest.mark.parametrize(
    "translator", [OllamaTranslator, OpenAICompatibleTranslator],
)
@pytest.mark.parametrize(
    ("language", "required", "excluded"),
    [("en", "칭의 -> justification", "칭의 -> 義認"),
     ("ja", "칭의 -> 義認", "칭의 -> justification")],
)
def test_language_specific_glossary_prompt(
    translator, language: str, required: str, excluded: str,
) -> None:
    context = SessionContext(
        glossary=[
            GlossaryEntry(
                term="칭의", translations={"en": "justification", "ja": "義認"},
            ),
            GlossaryEntry(
                term="폐기", translations={"ja": "不要"}, enabled=False,
            ),
        ]
    )
    prompt = translator.build_prompt(
        "오늘은 칭의에 관해 설명합니다.",
        source_language="ko",
        target_language=language,
        context=context,
    )
    assert required in prompt
    assert excluded not in prompt
    assert "폐기 -> 不要" not in prompt
    assert prompt.endswith("오늘은 칭의에 관해 설명합니다.")
