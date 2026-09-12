from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from difflib import SequenceMatcher

import httpx

from .models import SessionContext

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
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,:/-]\d+)*")
_SPACE_PATTERN = re.compile(r"\s+")


class CorrectionError(RuntimeError):
    """A constrained correction provider could not safely produce a result."""


class ConstrainedCorrector(ABC):
    provider = "base"
    model: str | None = None

    async def prepare(self) -> None:
        return None

    @abstractmethod
    async def correct(
        self,
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class OllamaConstrainedCorrector(ConstrainedCorrector):
    provider = "ollama"

    def __init__(self, *, base_url: str, model: str, request_timeout_seconds: float = 8.0) -> None:
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
            raise CorrectionError(f"Ollama is unavailable: {exc}") from exc

        names = {
            str(item.get("name") or item.get("model") or "")
            for item in payload.get("models", [])
        }
        family = self.model.split(":", 1)[0]
        if not any(name == self.model or name.split(":", 1)[0] == family for name in names):
            raise CorrectionError(
                f"Ollama correction model '{self.model}' is not installed. "
                f"Run: ollama pull {self.model}"
            )

    async def correct(
        self,
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        if not text.strip():
            return text

        prompt = self.build_prompt(
            text,
            source_language=source_language,
            context=context,
        )
        try:
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise CorrectionError(f"Ollama correction failed: {exc}") from exc

        content = str(payload.get("message", {}).get("content", "")).strip()
        if not content:
            raise CorrectionError("Ollama returned an empty correction response")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise CorrectionError("Ollama correction response is not valid JSON") from exc

        candidate = str(parsed.get("corrected_text", "")).strip()
        return validate_constrained_candidate(text, candidate)

    @staticmethod
    def build_prompt(
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        source_name = _LANGUAGE_NAMES.get(source_language, source_language)
        lines = [
            f"You correct streaming ASR transcripts written in {source_name} ({source_language}).",
            "Correct only obvious speech-recognition, spacing, punctuation, and proper-noun errors.",
            "Do not translate, summarize, paraphrase, explain, complete unfinished thoughts, or add facts.",
            "Preserve the speaker's meaning, negation, numbers, named entities, tone, and sentence order.",
            "If uncertain, keep the original wording.",
            'Return exactly one JSON object: {"corrected_text":"..."}.',
        ]

        if context.title:
            lines.append(f"Session title: {context.title}")
        if context.presenter:
            lines.append(f"Presenter: {context.presenter}")

        mappings: list[str] = []
        for entry in context.glossary:
            if not entry.enabled:
                continue
            if entry.aliases:
                mappings.append(f"- {', '.join(entry.aliases)} -> {entry.term}")
            else:
                mappings.append(f"- canonical term: {entry.term}")
        if mappings:
            lines.append("Terminology hints; apply only when the spoken text supports them:")
            lines.extend(mappings[:80])

        reference = context.reference_excerpt(3000)
        if reference:
            lines.extend(
                [
                    "Reference material follows. Use it only to disambiguate terminology and names. "
                    "Never copy facts from it unless they are present in the transcript:",
                    "--- reference ---",
                    reference,
                    "--- end reference ---",
                ]
            )

        lines.extend(["Transcript to correct:", text])
        return "\n".join(lines)


def validate_constrained_candidate(original: str, candidate: str) -> str:
    """Reject output that looks like translation, summarization, or factual rewriting."""

    original = original.strip()
    candidate = candidate.strip()
    if not candidate:
        raise CorrectionError("correction candidate is empty")
    if candidate == original:
        return candidate

    original_numbers = _NUMBER_PATTERN.findall(original)
    candidate_numbers = _NUMBER_PATTERN.findall(candidate)
    if candidate_numbers != original_numbers:
        raise CorrectionError("correction candidate changed numeric tokens")

    original_compact = _SPACE_PATTERN.sub("", original)
    candidate_compact = _SPACE_PATTERN.sub("", candidate)
    original_length = max(1, len(original_compact))
    candidate_length = len(candidate_compact)
    length_ratio = candidate_length / original_length
    if not 0.55 <= length_ratio <= 1.55:
        raise CorrectionError("correction candidate changed transcript length too aggressively")

    similarity = SequenceMatcher(None, original_compact, candidate_compact).ratio()
    if original_length >= 12 and similarity < 0.42:
        raise CorrectionError("correction candidate diverged too far from the source transcript")

    return candidate
