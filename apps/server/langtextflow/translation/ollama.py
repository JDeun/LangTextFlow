from __future__ import annotations

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


class OllamaTranslator(Translator):
    provider = "ollama"

    def __init__(self, *, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def prepare(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise TranslationError(f"Ollama is unavailable: {exc}") from exc

        names = {
            str(item.get("name") or item.get("model") or "")
            for item in payload.get("models", [])
        }
        family = self.model.split(":", 1)[0]
        if not any(name == self.model or name.split(":", 1)[0] == family for name in names):
            raise TranslationError(
                f"Ollama model '{self.model}' is not installed. Run: ollama pull {self.model}"
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
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise TranslationError(f"Ollama translation failed: {exc}") from exc

        content = str(payload.get("message", {}).get("content", "")).strip()
        if not content:
            raise TranslationError("Ollama returned an empty translation")
        return content

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
            f"Produce only the {target_name} translation, without explanations or commentary.",
        ]
        terms = []
        for entry in context.glossary:
            translated = entry.translations.get(target_language)
            if entry.enabled and translated:
                terms.append(f"- {entry.term} -> {translated}")
        if terms:
            lines.append("Use these terminology mappings when applicable:")
            lines.extend(terms)
        lines.append(f"Please translate the following {source_name} text into {target_name}:")
        return "\n".join(lines) + f"\n\n\n{text}"
