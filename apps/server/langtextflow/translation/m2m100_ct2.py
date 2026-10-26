"""Opt-in local CPU M2M100 translation runtime; no implicit model download.

This module exposes a product provider while keeping the six-direction,
real-audio quality gate separate from syntactic adapter unit tests.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

from ..models import SessionContext
from .base import TranslationError, Translator

SUPPORTED_LANGUAGES = frozenset({"ko", "en", "ja"})
MODEL_ID = "facebook/m2m100_418M"

# Process-wide bound: timed-out asyncio callers cannot cancel a running native
# inference. Limit the model to one CPU worker and three queued requests even
# if multiple sessions are stopped and restarted while inference is in flight.
_MAX_NATIVE_JOBS = 4
_NATIVE_JOB_SLOTS = BoundedSemaphore(_MAX_NATIVE_JOBS)
_NATIVE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="langtextflow-m2m100-cpu",
)


def _release_native_job_slot(job: Future[str]) -> None:
    # Consume failures from orphaned/cancelled asyncio waiters; the worker
    # itself always finishes or fails before its capacity slot is reclaimed.
    try:
        if not job.cancelled():
            job.exception()
    finally:
        _NATIVE_JOB_SLOTS.release()


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
        model_ready = all(
            (self.model_dir / name).is_file()
            for name in ("model.bin", "config.json")
        )
        tokenizer_ready = all(
            (self.tokenizer_dir / name).is_file()
            for name in ("tokenizer_config.json", "sentencepiece.bpe.model")
        )
        if not model_ready or not tokenizer_ready:
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
        # Capture strong references *before* scheduling native CPU inference.
        # Closing a session may clear instance fields while an asyncio-cancelled
        # worker thread still runs. Captured resources stay valid until it exits.
        tokenizer = self._tokenizer
        engine = self._engine
        if not _NATIVE_JOB_SLOTS.acquire(blocking=False):
            raise TranslationError(
                "Local M2M100 CPU inference at capacity; source caption preserved"
            )
        try:
            job = _NATIVE_EXECUTOR.submit(
                self._translate_sync, text, source_language, target_language,
                tokenizer, engine,
            )
        except Exception as exc:
            _NATIVE_JOB_SLOTS.release()
            raise TranslationError("Local M2M100 inference scheduling failed") from exc

        job.add_done_callback(_release_native_job_slot)
        try:
            # Shield the native future from asyncio cancellation. A timed-out
            # session stops awaiting but cannot silently release its slot until
            # the underlying native operation actually terminates.
            return await asyncio.shield(asyncio.wrap_future(job))
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError("Local M2M100 inference failed") from exc

    def _translate_sync(
        self, text: str, source: str, target: str, tokenizer: Any, engine: Any,
    ) -> str:
        with self._inference_lock:
            tokenizer.src_lang = source
            source_tokens = tokenizer.convert_ids_to_tokens(tokenizer.encode(text))
            prefix = tokenizer.lang_code_to_token[target]
            result = engine.translate_batch(
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
        # Native CPU inference cannot be preempted: bounded process-wide jobs
        # retain strong engine/tokenizer references until the thread exits.
        # Closing only prevents new submissions through this instance.
        self._engine = None
        self._tokenizer = None
