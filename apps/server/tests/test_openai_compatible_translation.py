from __future__ import annotations

import pytest

from langtextflow.models import GlossaryEntry, SessionContext
from langtextflow.translation.openai_compatible import OpenAICompatibleTranslator


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, object]:
        return self.payload


class FakeClient:
    requests: list[tuple[str, str, dict[str, object]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append(("GET", url, kwargs))
        return FakeResponse({"data": [{"id": "local-translator"}]})

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append(("POST", url, kwargs))
        return FakeResponse(
            {
                "choices": [
                    {"message": {"content": "Today we will read John chapter 3."}}
                ]
            }
        )


@pytest.mark.asyncio
async def test_openai_compatible_translator_preflight_and_translate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeClient.requests = []
    monkeypatch.setattr(
        "langtextflow.translation.openai_compatible.httpx.AsyncClient",
        FakeClient,
    )
    translator = OpenAICompatibleTranslator(
        base_url="http://127.0.0.1:1234/v1/",
        model="local-translator",
        api_key="secret-token",
    )
    context = SessionContext(
        glossary=[
            GlossaryEntry(
                term="요한복음",
                translations={"en": "John"},
            )
        ],
        reference_text="본문은 요한복음 3장입니다.",
    )

    await translator.prepare()
    translated = await translator.translate(
        "오늘 요한복음 3장을 보겠습니다",
        source_language="ko",
        target_language="en",
        context=context,
    )

    assert translated == "Today we will read John chapter 3."
    assert FakeClient.requests[0][1] == "http://127.0.0.1:1234/v1/models"
    assert FakeClient.requests[1][1] == "http://127.0.0.1:1234/v1/chat/completions"
    for _, _, kwargs in FakeClient.requests:
        assert kwargs["headers"] == {"Authorization": "Bearer secret-token"}

    payload = FakeClient.requests[1][2]["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "local-translator"
    assert payload["temperature"] == 0
    message = payload["messages"][0]
    assert "요한복음 -> John" in message["content"]
    assert "Never add unspoken information" in message["content"]


def test_openai_compatible_translator_requires_model() -> None:
    with pytest.raises(ValueError, match="model is required"):
        OpenAICompatibleTranslator(
            base_url="http://127.0.0.1:1234/v1",
            model="",
        )
