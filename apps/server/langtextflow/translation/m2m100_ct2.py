"""Opt-in local CPU M2M100 translation runtime; no implicit model download.

This module exposes a product provider while keeping the six-direction,
real-audio quality gate separate from syntactic adapter unit tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

from ..models import MAX_TRANSCRIPT_TEXT_CHARS, MAX_TRANSLATION_TEXT_CHARS, SessionContext
from .base import TranslationError, TranslationRejected, Translator
from .glossary_selection import _mentioned, relevant_glossary

SUPPORTED_LANGUAGES = frozenset({"ko", "en", "ja"})
MODEL_ID = "facebook/m2m100_418M"
_MODEL_FILES = ("model.bin", "config.json")
_TOKENIZER_FILES = ("tokenizer_config.json", "sentencepiece.bpe.model", "vocab.json")


def missing_m2m100_cache_files(model_dir: str | Path, tokenizer_dir: str | Path) -> list[str]:
    """Share the local-file contract between preflight and native initialization."""
    missing = []
    for label, directory, names in (
        ("CTranslate2", Path(model_dir), _MODEL_FILES),
        ("M2M100 tokenizer", Path(tokenizer_dir), _TOKENIZER_FILES),
    ):
        absent = [name for name in names if not (directory / name).is_file()]
        if absent:
            missing.append(f"{label}: {', '.join(absent)}")
    return missing


# One process/model and at most four admitted inference requests across sessions.
_NATIVE_JOB_SLOTS = BoundedSemaphore(4)
_CHILD_SLOT = BoundedSemaphore(1)
IPC_LIMIT = 128 * 1024
LOAD_TIMEOUT_SECONDS = 30.0
INFERENCE_TIMEOUT_SECONDS = 30.0
TERMINATE_GRACE_SECONDS = 1.0
REAP_TIMEOUT_SECONDS = 3.0


def worker_command(model_dir: Path, tokenizer_dir: Path, cpu_threads: int) -> list[str]:
    prefix = ([sys.executable, "--m2m100-worker"] if getattr(sys, "frozen", False)
              else [sys.executable, "-m", "langtextflow.translation.m2m100_worker"])
    return [*prefix, str(model_dir), str(tokenizer_dir), str(cpu_threads)]


class _M2M100Worker:
    """Own one killable native model, including cancellation during process launch."""

    def __init__(self, command: list[str]) -> None:
        if not _CHILD_SLOT.acquire(blocking=False):
            raise TranslationError("Local M2M100 CPU worker at capacity; retry after stop")
        self.process = None
        self.ready = False
        self._released = False
        self.released = asyncio.Event()
        self._closing: asyncio.Task | None = None
        self._late_cleanup: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0
        # No model downloads, tokenizer fallback, inherited speech logs or native stdout.
        self._launch = asyncio.create_task(asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=IPC_LIMIT,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                 "USE_TORCH": "0", "PYINSTALLER_RESET_ENVIRONMENT": "0"},
        ))
        self._launch.add_done_callback(self._schedule_late_spawn_cleanup)

    def _schedule_late_spawn_cleanup(self, finished: asyncio.Task) -> None:
        # A slow OS spawn can finish after bounded cancellation cleanup returned.
        if (self._launch.done() and self._closing is not None and self._closing.done()
                and self.process is None and not self._released
                and (self._late_cleanup is None or self._late_cleanup.done())):
            self._late_cleanup = asyncio.create_task(self.close())
            self._late_cleanup.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )

    async def start(self, *, timeout: float | None = None) -> None:
        async with asyncio.timeout(LOAD_TIMEOUT_SECONDS if timeout is None else timeout):
            self.process = await asyncio.shield(self._launch)
            if await self._read() != {"ready": True}:
                raise TranslationError("Local M2M100 CPU worker could not initialize")
            self.ready = True

    async def _read(self) -> dict:
        line = await self.process.stdout.readline()
        if not line or len(line) >= IPC_LIMIT:
            raise TranslationError("Local M2M100 CPU worker failed")
        item = json.loads(line)
        if not isinstance(item, dict):
            raise TranslationError("Invalid local M2M100 worker response")
        return item

    async def translate(self, text: str, source: str, target: str) -> str:
        # Cancellation while waiting for this lock owns no active worker request.
        async with self._lock:
            if not self.ready:
                raise TranslationError("Local M2M100 provider is not prepared")
            try:
                async with asyncio.timeout(INFERENCE_TIMEOUT_SECONDS):
                    self._request_id += 1
                    request_id = self._request_id
                    message = json.dumps({"id": request_id, "text": text,
                                          "source": source, "target": target},
                                         ensure_ascii=False) + "\n"
                    self.process.stdin.write(message.encode("utf-8"))
                    await self.process.stdin.drain()
                    result = await self._read()
                    if (set(result) == {"id", "error"}
                            and type(result["id"]) is int and result["id"] == request_id
                            and result["error"] == "translation_rejected"):
                        raise TranslationRejected("Local M2M100 translated caption rejected")
                    candidate = result.get("text")
                    if (set(result) != {"id", "text"}
                            or type(result.get("id")) is not int or result.get("id") != request_id
                            or not isinstance(candidate, str)
                            or not candidate.strip()
                            or len(candidate) > MAX_TRANSLATION_TEXT_CHARS):
                        raise TranslationError("Invalid local M2M100 translation response")
                    return candidate
            except TranslationRejected:
                raise
            except BaseException:
                await self.close()
                raise

    async def close(self) -> None:
        self.ready = False
        if self._closing is None or self._closing.done() and self._closing.exception():
            self._closing = asyncio.create_task(self._terminate())
            self._closing.add_done_callback(self._schedule_late_spawn_cleanup)
        # Repeated cancellation cannot release admission before the OS child is reaped.
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        self._closing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _terminate(self) -> None:
        try:
            process = await asyncio.wait_for(asyncio.shield(self._launch), REAP_TIMEOUT_SECONDS)
        except TimeoutError:
            # Retain admission until the late-spawn callback kills and reaps this owner.
            raise TranslationError("Local M2M100 process launch cleanup timed out") from None
        except Exception:
            self._release()
            return
        self.process = process
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), TERMINATE_GRACE_SECONDS)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
        # Keep the single-child lease on an OS reap failure; never spawn another model.
        reap = asyncio.create_task(process.wait())
        reap.add_done_callback(lambda done: self._release() if not done.cancelled() else None)
        await asyncio.wait_for(asyncio.shield(reap), REAP_TIMEOUT_SECONDS)

    def _release(self) -> None:
        if not self._released:
            self._released = True
            _CHILD_SLOT.release()
            self.released.set()


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
        self._worker: _M2M100Worker | None = None
        self._prepare_lock = asyncio.Lock()
        self._generation = 0
        self._recovery_attempt = 0
        self._closed = True
        # The tokenizer's src_lang is mutable and must never be shared between
        # simultaneous EN/JA/KO calls without serialization.
        self._inference_lock = Lock()

    def _check_cache(self) -> None:
        if missing_m2m100_cache_files(self.model_dir, self.tokenizer_dir):
            raise TranslationError(
                "Local M2M100 model/tokenizer cache is missing. "
                "Use the pinned CPU model provisioning process; no cloud fallback."
            )

    def _check_generation(self, generation: int) -> None:
        if self._closed or self._generation != generation:
            raise TranslationError("Local M2M100 provider is not prepared or was stopped")

    async def prepare(self) -> None:
        self._check_cache()
        self._generation += 1
        generation = self._generation
        self._closed = False
        try:
            async with self._prepare_lock:
                self._check_generation(generation)
                await self._start_worker(generation)
        except BaseException:
            if self._generation == generation:
                self._closed = True
            raise

    async def _start_worker(self, generation: int) -> None:
        self._check_generation(generation)
        self._check_cache()
        previous = self._worker
        if previous is not None:
            # A failed child's capacity is not reusable until close confirms reap.
            await previous.close()
            if self._worker is previous:
                self._worker = None
        self._check_generation(generation)
        worker = _M2M100Worker(worker_command(self.model_dir, self.tokenizer_dir, self.cpu_threads))
        self._worker = worker
        try:
            await worker.start()
            self._check_generation(generation)
            if self._worker is not worker or not worker.ready:
                raise TranslationError("Local M2M100 initialization was stopped")
        except BaseException as exc:
            try:
                await worker.close()
            finally:
                if worker._released and self._worker is worker:
                    self._worker = None
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise TranslationError(
                "Local M2M100 CPU provider could not initialize; "
                "check CT2 weights, tokenizer and optional dependencies."
            ) from exc

    def _prepare_sync(
        self, *, report_phase: Callable[[str], None] | None = None,
    ) -> tuple[Any, Any]:
        if report_phase is not None:
            report_phase("native_import_ct2")
        import ctranslate2

        if report_phase is not None:
            report_phase("native_import_tokenizer")
        from transformers import M2M100Tokenizer

        if report_phase is not None:
            report_phase("native_tokenizer_load")
        tokenizer = M2M100Tokenizer.from_pretrained(
            str(self.tokenizer_dir), local_files_only=True
        )
        if report_phase is not None:
            report_phase("native_engine_load")
        engine = ctranslate2.Translator(
            str(self.model_dir), device="cpu", compute_type="int8",
            inter_threads=1, intra_threads=self.cpu_threads,
        )
        if report_phase is not None:
            report_phase("native_language_check")
        for language in SUPPORTED_LANGUAGES:
            if language not in tokenizer.lang_code_to_token:
                raise ValueError("M2M100 local tokenizer lacks a required language code")
        return tokenizer, engine

    async def translate(
        self, text: str, *, source_language: str,
        target_language: str, context: SessionContext,
    ) -> str:
        if (source_language not in SUPPORTED_LANGUAGES
                or target_language not in SUPPORTED_LANGUAGES
                or source_language == target_language):
            raise TranslationError("M2M100 supports only distinct ko/en/ja language pairs")
        if not text.strip():
            raise TranslationError("Cannot translate an empty source caption")
        if len(text) > MAX_TRANSCRIPT_TEXT_CHARS:
            raise TranslationError("Local M2M100 source caption is too long")
        if not _NATIVE_JOB_SLOTS.acquire(blocking=False):
            raise TranslationError(
                "Local M2M100 CPU inference at capacity; source caption preserved"
            )
        try:
            candidate = await self._translate_rpc(text, source_language, target_language)
            # Do not silently publish a mistranslated operator-defined term.
            # This is validation, not unsupported forced decoding: fail the
            # target explicitly while retaining the original source caption.
            self._check_glossary(candidate, text, source_language, target_language, context)
            return candidate
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError("Local M2M100 inference failed") from exc

        finally:
            _NATIVE_JOB_SLOTS.release()

    @staticmethod
    def _check_glossary(candidate: str, text: str, source: str, target: str,
                        context: SessionContext) -> None:
        for entry, required in relevant_glossary(
            context, text, target, source_language=source,
        ):
            if not _mentioned(candidate, required):
                raise TranslationRejected(
                    f"Local M2M100 glossary term not preserved: {entry.term}"
                )

    async def _translate_rpc(self, text: str, source: str, target: str) -> str:
        generation = self._generation
        self._check_generation(generation)
        worker = self._worker
        if worker is None or not worker.ready:
            failed_owner = worker
            attempt = self._recovery_attempt
            async with self._prepare_lock:
                self._check_generation(generation)
                worker = self._worker
                if worker is None or not worker.ready:
                    if worker is not failed_owner or self._recovery_attempt != attempt:
                        raise TranslationError("Local M2M100 recovery could not initialize")
                    # Recover on a later call only; never retry the failed caption.
                    self._recovery_attempt += 1
                    await self._start_worker(generation)
                    worker = self._worker
        self._check_generation(generation)
        result = await worker.translate(text, source, target)
        if (self._generation != generation or self._closed
                or self._worker is not worker or not worker.ready):
            raise TranslationError("Local M2M100 translation was stopped")
        return result

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
            if (not candidate or len(candidate) > MAX_TRANSLATION_TEXT_CHARS
                    or candidate.casefold() == text.strip().casefold()
                    or candidate.startswith(("{", "["))):
                raise TranslationRejected("Local M2M100 returned an invalid translated caption")
            return candidate

    async def close(self) -> None:
        # Invalidate queued recovery before awaiting process cleanup. An old load
        # cannot publish itself or spawn a replacement after operator stop.
        self._closed = True
        self._generation += 1
        worker = self._worker
        self._worker = None
        if worker is not None:
            await worker.close()
