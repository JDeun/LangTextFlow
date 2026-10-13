"""Relevant glossary selection avoids huge or misleading CPU MT prompts."""
from __future__ import annotations

import json

import pytest

from langtextflow.models import GlossaryEntry, ProductPreset, SessionContext
from langtextflow.translation.glossary_selection import relevant_glossary
from langtextflow.translation.ollama import OllamaTranslator
from langtextflow.translation.openai_compatible import OpenAICompatibleTranslator


def test_relevant_bible_books_precede_generic_substrings() -> None:
    context = SessionContext(
        preset=ProductPreset.CHURCH,
        glossary=[
            GlossaryEntry(term="복음", translations={"en": "gospel"}),
            GlossaryEntry(term="요한복음", translations={"en": "Gospel of John"}),
            GlossaryEntry(term="성령", translations={"ja": "聖霊"}),
            GlossaryEntry(
                term="컨퍼런스", translations={"en": "conference"},
                presets=[ProductPreset.CONFERENCE],
            ),
        ],
    )
    hits = relevant_glossary(context, "요한복음 3장 16절", "en")
    assert [entry.term for entry, _ in hits] == ["요한복음", "복음"]
    assert relevant_glossary(context, "요한복음 3장 16절", "ja") == []


def test_alias_and_latin_whole_word() -> None:
    context = SessionContext(glossary=[
        GlossaryEntry(term="칭의", aliases=["칭이"], translations={"ja": "義認"}),
        GlossaryEntry(term="cross", translations={"ko": "십자가"}),
    ])
    assert relevant_glossary(context, "칭이를 말합니다", "ja")[0][0].term == "칭의"
    assert relevant_glossary(context, "crossing the road", "ko") == []
    assert relevant_glossary(context, "at the cross.", "ko")[0][0].term == "cross"


def test_many_preset_terms_have_bounded_prompt_hints() -> None:
    context = SessionContext(glossary=[
        GlossaryEntry(term=f"용어{i}", translations={"en": f"term{i}"})
        for i in range(300)
    ])
    assert relevant_glossary(context, "관련 없음", "en") == []
    text = " ".join(f"용어{i}" for i in range(300))
    assert len(relevant_glossary(context, text, "en")) == 24
    with pytest.raises(ValueError, match="negative"):
        relevant_glossary(context, "용어1", "en", limit=-1)


@pytest.mark.parametrize("translator", [OllamaTranslator, OpenAICompatibleTranslator])
def test_provider_only_includes_source_mentioned_terms(translator) -> None:
    context = SessionContext(
        preset=ProductPreset.CHURCH,
        glossary=[
            GlossaryEntry(term="성령", translations={"en": "Holy Spirit", "ja": "聖霊"}),
            GlossaryEntry(term="요한복음", translations={"en": "Gospel of John"}),
        ],
    )
    text = "성령에 대해서 말씀합니다."
    prompt = translator.build_prompt(
        text, source_language="ko", target_language="en", context=context,
    )
    assert "성령 -> Holy Spirit" in prompt
    assert "요한복음 -> Gospel of John" not in prompt
    messages = translator.build_messages(
        text, source_language="ko", target_language="en", context=context,
    )
    structured = json.loads(messages[1]["content"])
    assert structured["terminology_mappings"] == [
        {"source": "성령", "target": "Holy Spirit"}
    ]
