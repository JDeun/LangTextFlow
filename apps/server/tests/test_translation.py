import pytest

from langtextflow.models import GlossaryEntry, SessionContext
from langtextflow.translation.base import TranslationError
from langtextflow.translation.ollama import OllamaTranslator


def test_ollama_prompt_contains_target_glossary_mapping() -> None:
    context = SessionContext(
        glossary=[
            GlossaryEntry(
                term="칭의",
                aliases=["의롭다 하심"],
                translations={"en": "justification"},
            )
        ]
    )
    prompt = OllamaTranslator.build_prompt(
        "우리는 칭의에 대해 살펴보겠습니다.",
        source_language="ko",
        target_language="en",
        context=context,
    )
    assert "Korean (ko)" in prompt
    assert "English (en)" in prompt
    assert "칭의 -> justification" in prompt
    assert prompt.endswith("\n\n\n우리는 칭의에 대해 살펴보겠습니다.")


def test_ollama_prompt_uses_reference_only_as_disambiguation_context() -> None:
    context = SessionContext(
        reference_text="Speaker: John Smith\nTopic: justification and sanctification"
    )

    prompt = OllamaTranslator.build_prompt(
        "오늘은 칭의에 대해 말하겠습니다.",
        source_language="ko",
        target_language="en",
        context=context,
    )

    assert "--- reference ---" in prompt
    assert "John Smith" in prompt
    assert "Never add information that was not spoken" in prompt
    assert prompt.endswith("\n\n\n오늘은 칭의에 대해 말하겠습니다.")


class _BadPrepareResponse:
    def raise_for_status(self) -> None:
        return

    def json(self) -> list[object]:
        return []


class _BadPrepareClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "_BadPrepareClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def get(self, url: str) -> _BadPrepareResponse:
        assert url.endswith("/api/tags")
        return _BadPrepareResponse()


@pytest.mark.asyncio
async def test_ollama_prepare_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("langtextflow.translation.ollama.httpx.AsyncClient", _BadPrepareClient)
    translator = OllamaTranslator(base_url="http://127.0.0.1:11434", model="test-model")

    with pytest.raises(TranslationError, match="JSON object"):
        await translator.prepare()
