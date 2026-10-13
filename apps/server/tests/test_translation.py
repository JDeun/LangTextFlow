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


def test_translategemma_official_single_user_message_has_plain_source() -> None:
    from langtextflow.translation.ollama import OllamaTranslator

    context = SessionContext(title="Sample church service")
    messages = OllamaTranslator.build_translategemma_messages(
        "요한복음 3장 16절을 읽겠습니다.",
        source_language="ko",
        target_language="en",
        context=context,
    )
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"].endswith(
        "\\n\\n\\n요한복음 3장 16절을 읽겠습니다."
    )
    assert "session_title" not in messages[0]["content"]
    assert "source_text" not in messages[0]["content"]


@pytest.mark.parametrize(
    "candidate",
    [
        '{"session_title":"Test","source_text":"Here is the translated sentence."}',
        '{"source_text":"Translated.","reference_material":""}',
        '{"translation":"Valid","session_title":"Leaked"}',
        '[{"text":"Translated"}]',
        '{"source_text":',
    ],
)
def test_ollama_rejects_context_leaks_in_caption(candidate: str) -> None:
    with pytest.raises(ValueError, match="context envelope|malformed"):
        OllamaTranslator.validate_caption_text(candidate)


def test_ollama_can_accept_explicit_translation_only_json() -> None:
    assert OllamaTranslator.validate_caption_text(
        '{"translation":"The Lord is our shepherd."}'
    ) == "The Lord is our shepherd."


class _TranslateGemmaResponse:
    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, object]:
        return {"message": {"content": "Let us read John 3:16 together."}}


class _SpyTranslateGemmaClient:
    calls: list[dict[str, object]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> _SpyTranslateGemmaClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def post(self, url: str, *, json: dict[str, object]) -> _TranslateGemmaResponse:
        assert url.endswith("/api/chat")
        self.calls.append(json)
        return _TranslateGemmaResponse()


@pytest.mark.asyncio
async def test_translategemma_http_uses_official_plain_text_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _SpyTranslateGemmaClient.calls = []
    monkeypatch.setattr(
        "langtextflow.translation.ollama.httpx.AsyncClient", _SpyTranslateGemmaClient
    )
    translator = OllamaTranslator(base_url="http://127.0.0.1:11434", model="translategemma:4b")
    translated = await translator.translate(
        "요한복음 3장 16절 말씀을 함께 읽겠습니다.",
        source_language="ko",
        target_language="en",
        context=SessionContext(title="Test church session"),
    )
    assert translated == "Let us read John 3:16 together."
    payload = _SpyTranslateGemmaClient.calls[0]
    assert payload["model"] == "translategemma:4b"
    assert payload["stream"] is False
    messages = payload["messages"]
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"].endswith(
        "\\n\\n\\n요한복음 3장 16절 말씀을 함께 읽겠습니다."
    )
    assert "source_text" not in messages[0]["content"]
    assert "session_title" not in messages[0]["content"]
