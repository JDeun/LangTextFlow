"""Opt-in local CPU M2M100 translation runtime; no implicit model download.

This module exposes a product provider while keeping the six-direction,
real-audio quality gate separate from syntactic adapter unit tests.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock
from typing import Any

from ..models import SessionContext
from .base import TranslationError, Translator

SUPPORTED_LANGUAGES = frozenset({"ko", "en", "ja"})
MODEL_ID = "facebook/m2m100_418M"


class M2M100CT2Translator(Translator):
    """CTranslate2 CPU INT8 adapter using local pinned model/tokenizer files."""

    provider = "m2m100-ct2"
    model = MODEL_ID

    def __init__(self, *, model_dir: str, tokenizer_dir: str, cpu_threads: int = 2) -> None:
        if not 1 <= cpu_threads <= 16:
            raise ValueError("M2M100 CPU thread count must be between 1 and 16")
        self.model_dir = Path(model_dir)
        self.tokenizer_dir = Path(tokenizer_dir)
        self.cpu_threads = cpu_threads
        self._tokenizer: Any = None
        self._engine: Any = None
        # The tokenizer's src_lang is mutable and must never be shared between
        # simultaneous EN/JA/KO calls without serialization.
        self._inference_lock = Lock()

    async def prepare(self) -> None:
        if not self.model_dir.is_dir() or not self.tokenizer_dir.is_dir():
            raise TranslationError(
                "Local M2M100 model/tokenizer cache is missing. "
                "Use the pinned CPU model provisioning process; no cloud fallback."
            )
        try:
            await asyncio.to_thread(self._prepare_sync)
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            raise TranslationError(
                "Local M2M100 CPU provider could not initialize; "
                "check CT2 weights, tokenizer and optional dependencies."
            ) from exc

    def _prepare_sync(self) -> None:
        import ctranslate2
        from transformers import M2M100Tokenizer

        tokenizer = M2M100Tokenizer.from_pretrained(
            str(self.tokenizer_dir), local_files_only=True
        )
        engine = ctranslate2.Translator(
            str(self.model_dir), device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=self.cpu_threads,
        )
        for language in SUPPORTED_LANGUAGES:
            if language not in tokenizer.lang_code_to_token:
                raise ValueError("M2M100 local tokenizer lacks a required language code")
        self._tokenizer = tokenizer
        self._engine = engine

    async def translate(
        self, text: str, *, source_language: str,
        target_language: str, context: SessionContext,
    ) -> str:
        del context  # Glossary must be proven on generated candidates before enabling.
        if (source_language not in SUPPORTED_LANGUAGES
                or target_language not in SUPPORTED_LANGUAGES
                or source_language == target_language):
            raise TranslationError("M2M100 supports only distinct ko/en/ja language pairs")
        if self._engine is None or self._tokenizer is None:
            raise TranslationError("Local M2M100 provider is not prepared")
        if not text.strip():
            raise TranslationError("Cannot translate an empty source caption")
        try:
            return await asyncio.to_thread(self._translate_sync, text, source_language,
                                           target_language)
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError("Local M2M100 inference failed") from exc

    def _translate_sync(self, text: str, source: str, target: str) -> str:
        with self._inference_lock:
            tokenizer = self._tokenizer
            tokenizer.src_lang = source
            source_tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(text))
            prefix = tokenizer.lang_code_to_token[target]
            result = self._engine.translate_batch(
                [source_tokens], target_prefix=[[prefix]],
                beam_size=1, max_decoding_length=256,
            )
            if not result or not result[0].hypotheses:
                raise TranslationError("Local M2M100 returned no translation")
            tokens = result[0].hypotheses[0]
            if not tokens or tokens[0] != prefix:
                raise TranslationError("Local M2M100 violated the target language prefix")
            target_ids = tokenizer.convert_tokens_to_ids(tokens[1:])
            candidate = tokenizer.decode(target_ids, skip_special_tokens=True).strip()
            if (not candidate or candidate.casefold() == text.strip().casefold()
                    or candidate.startswith(("{", "["))):
                raise TranslationError("Local M2M100 returned an invalid translated caption")
            return candidate

    async def close(self) -> None:
        # CTranslate2 inference in a Python worker thread cannot be forcibly
        # stopped by asyncio cancellation. Releasing references is not proof
        # of a terminated native call. Full teardown needs #157 lifecycle work.
        self._engine = None
        self._tokenizer = None
