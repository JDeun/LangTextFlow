from __future__ import annotations

import httpx

from ..models import SessionContext
from ..prompt_safety import UNTRUSTED_DATA_POLICY, bounded_model_text, untrusted_json
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

    def __init__(
        self, *, base_url: str, model: str, request_timeout_seconds: float = 20.0
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.request_timeout_seconds = request_timeout_seconds

    async def prepare(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise TranslationError(f"Ollama is unavailable: {exc}") from exc

        if not isinstance(payload, dict):
            raise TranslationError("Ollama model list response must be a JSON object")
        models = payload.get("models", [])
        if not isinstance(models, list):
            raise TranslationError("Ollama model list response models field must be a list")
        names = {
            str(item.get("name") or item.get("model") or "")
            for item in models
            if isinstance(item, dict)
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
        # TranslateGemma is trained for one plain-text user prompt with two
        # blank lines before the source. Passing a JSON context object made the
        # 4B model translate the *entire JSON envelope* into public captions.
        # Other Ollama instruction models retain the existing structured path.
        if self.model.split(":", 1)[0].casefold() == "translategemma":
            messages = self.build_translategemma_messages(
                text,
                source_language=source_language,
                target_language=target_language,
                context=context,
            )
        else:
            messages = self.build_messages(
                text,
                source_language=source_language,
                target_language=target_language,
                context=context,
            )
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "stream": False,
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise TranslationError(f"Ollama translation failed: {exc}") from exc

        try:
            content = bounded_model_text(
                payload.get("message", {}).get("content", "")
                if isinstance(payload, dict)
                else "",
                label="Ollama translation",
            )
            return self.validate_caption_text(content)
        except ValueError as exc:
            raise TranslationError(str(exc)) from exc

    @staticmethod
    def validate_caption_text(content: str) -> str:
        """Do not display model-generated context dictionaries as translations."""
        import json

        candidate = content.strip()
        if candidate.startswith(("{", "[")):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                raise ValueError("Ollama translation emitted malformed structured text")
            if isinstance(parsed, dict) and set(parsed) == {"translation"}:
                return bounded_model_text(parsed["translation"], label="Ollama translation")
            raise ValueError("Ollama translation returned a context envelope, not a caption")
        return candidate

    @staticmethod
    def build_translategemma_messages(
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> list[dict[str, str]]:
        """Official single-user TranslateGemma prompt, never a metadata JSON blob."""
        return [
            {
                "role": "user",
                "content": OllamaTranslator.build_prompt(
                    text,
                    source_language=source_language,
                    target_language=target_language,
                    context=context,
                ),
            }
        ]

    @staticmethod
    def build_messages(
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> list[dict[str, str]]:
        source_name = _LANGUAGE_NAMES.get(source_language, source_language)
        target_name = _LANGUAGE_NAMES.get(target_language, target_language)
        system = "\n".join(
            [
                f"Translate from {source_name} ({source_language}) to {target_name} "
                f"({target_language}).",
                "Accurately preserve the meaning, nuance, negation, numbers, and named entities.",
                f"Return only the {target_name} translation, without explanations or commentary.",
                "Never add information that was not spoken.",
                UNTRUSTED_DATA_POLICY,
            ]
        )
        terms: list[dict[str, str]] = []
        for entry in context.glossary:
            translated = entry.translations.get(target_language)
            if entry.enabled and translated:
                terms.append({"source": entry.term, "target": translated})
        user = untrusted_json(
            {
                "session_title": context.title,
                "presenter": context.presenter,
                "terminology_mappings": terms[:200],
                "reference_material": context.reference_excerpt(5000),
                "source_text": text,
            }
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    @staticmethod
    def build_prompt(
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> str:
        """Human-readable debug representation retained for tests/documentation."""

        source_name = _LANGUAGE_NAMES.get(source_language, source_language)
        target_name = _LANGUAGE_NAMES.get(target_language, target_language)
        lines = [
            f"You are a professional {source_name} ({source_language}) to "
            f"{target_name} ({target_language}) translator.",
            "Accurately preserve the meaning and nuance of the source text.",
            f"Produce only the {target_name} translation, without explanations or commentary.",
            UNTRUSTED_DATA_POLICY,
        ]
        terms = []
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
                    "Reference material follows. Use it only to disambiguate names, terminology, "
                    "and topic context. Never add information that was not spoken:",
                    "--- reference ---",
                    reference,
                    "--- end reference ---",
                ]
            )
        lines.append(f"Please translate the following {source_name} text into {target_name}:")
        return "\n".join(lines) + f"\n\n\n{text}"
