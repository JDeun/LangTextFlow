from __future__ import annotations

from typing import Any

import httpx

from ..models import SessionContext
from .base import TranslationError, Translator

_LANGUAGE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
}


class OpenAICompatibleTranslator(Translator):
    """Translator for OpenAI-compatible /v1 chat-completions servers."""

    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        request_timeout_seconds: float = 20.0,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        normalized_model = model.strip()
        if not normalized_url:
            raise ValueError("OpenAI-compatible base URL is required")
        if not normalized_model:
            raise ValueError("OpenAI-compatible translation model is required")
        self.base_url = normalized_url
        self.model = normalized_model
        self.api_key = api_key.strip()
        self.request_timeout_seconds = max(request_timeout_seconds, 0.1)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def prepare(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise TranslationError(
                f"OpenAI-compatible endpoint is unavailable: {exc}"
            ) from exc

        models = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids = {
            str(item.get("id", ""))
            for item in models
            if isinstance(item, dict) and item.get("id")
        }
        if self.model not in model_ids:
            raise TranslationError(
                f"OpenAI-compatible model '{self.model}' is not available"
            )

    async def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> str:
        if source_language == target_language:
            return text

        prompt = self.build_prompt(
            text,
            source_language=source_language,
            target_language=target_language,
            context=context,
        )
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise TranslationError(
                f"OpenAI-compatible translation failed: {exc}"
            ) from exc

        content = self._message_content(payload)
        if not content:
            raise TranslationError(
                "OpenAI-compatible endpoint returned an empty translation"
            )
        return content

    @staticmethod
    def _message_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip()
        return ""

    @staticmethod
    def build_prompt(
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> str:
        source_name = _LANGUAGE_NAMES.get(source_language, source_language)
        target_name = _LANGUAGE_NAMES.get(target_language, target_language)
        lines = [
            f"You are a professional {source_name} ({source_language}) to "
            f"{target_name} ({target_language}) translator.",
            "Accurately preserve the meaning and nuance of the source text.",
            f"Produce only the {target_name} translation, without commentary.",
        ]

        terms: list[str] = []
        for entry in context.glossary:
            translated = entry.translations.get(target_language)
            if entry.enabled and translated:
                terms.append(f"- {entry.term} -> {translated}")
        if terms:
            lines.append("Use these terminology mappings when applicable:")
            lines.extend(terms)

        reference = context.reference_excerpt(5000)
        if reference:
            lines.extend(
                [
                    "Reference material follows. Use it only to disambiguate names, "
                    "terminology, and topic context. Never add unspoken information:",
                    "--- reference ---",
                    reference,
                    "--- end reference ---",
                ]
            )

        lines.append(
            f"Please translate the following {source_name} text into {target_name}:"
        )
        return "\n".join(lines) + f"\n\n\n{text}"
