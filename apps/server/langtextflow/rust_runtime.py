"""Private transport to Rust authority; Python only mirrors canonical events.

This source-mode rollout deliberately rejects unsupported feature combinations.
Inference, network/cloud fallbacks and model provisioning are never added here.
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import sysconfig
import tempfile
import time
from contextlib import suppress
from pathlib import Path

from websockets.asyncio.client import connect

from .asr.base import AsrEngine, AsrEngineError
from .config import Settings
from .models import (
    CorrectionStatus,
    GlossaryEntry,
    StartSessionRequest,
    TranscriptEvent,
    TranslationStatus,
    public_model_identity,
)
from .telemetry import RustAuthorityMetrics, RustMetricsFrame
from .translation.factory import create_translator, provider_options, validate_local_pairs
from .whisper_cache import WHISPER_REPOSITORIES, managed_whisper_cache, resolve_whisper_model
from .worker_ipc import SUPPORTED_LANGUAGES

_ERROR = "Rust runtime unavailable; stop the session and check local runtime setup."
_ORIGIN = "http://127.0.0.1"
_MAX_FRAME = 1024 * 1024
_CONTROL_BUDGET = 15.0
_CONTEXT_LIMIT = 48 * 1024


def _source_context(request: StartSessionRequest) -> dict:
    context = request.context
    # These are exactly the fields consumed by existing ASR/LLM prompts and MT
    # guard. Preserve explicit hotwords; glossary hints require actual source.
    snapshot = {
        "title": context.title, "presenter": context.presenter,
        "description": context.description[:1000],
        "reference_text": context.reference_excerpt(5000),
        "preset": context.preset.value, "hotwords": list(context.hotwords),
        "glossary": [entry.model_dump(mode="json", include=set(GlossaryEntry.model_fields))
                     for entry in context.glossary
                     if entry.enabled and entry.applies_to(context.preset)
                     and entry.source_language == request.source_language],
    }
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(snapshot):
        size += len(chunk.encode())
        if size > _CONTEXT_LIMIT:
            raise ValueError("Rust inference context exceeds the 48KiB transport limit")
    return snapshot


def _python_worker(module: str, args: list[str]) -> dict:
    if sys.platform == "win32" and sys.prefix != sys.base_prefix:
        # Windows venv python.exe redirects to a second PID. Own the base
        # interpreter directly while loading the same trusted venv site files.
        bootstrap = (
            "import runpy,site,sys;site.addsitedir(sys.argv.pop(1));"
            "name=sys.argv.pop(1);runpy.run_module(name,run_name='__main__')"
        )
        return {"program": sys._base_executable, "args": [
            "-S", "-c", bootstrap, sysconfig.get_path("purelib"), module, *args,
        ]}
    return {"program": sys.executable, "args": ["-m", module, *args]}


def validate_rust_request(settings: Settings, request: StartSessionRequest) -> None:
    if (getattr(sys, "frozen", False) or request.engine not in {"auto", "faster-whisper"}
            or request.correction_provider not in {"none", "ollama"}
            or request.translation_provider not in {
                "none", "demo", "m2m100-ct2", "ollama", "openai-compatible",
            }
            or (request.correction_model and request.correction_provider != "ollama")
            or not 0 < settings.correction_timeout_seconds <= 120
            or (request.correction_provider == "ollama" and (
                len(settings.ollama_url.encode()) > 4096
                or not 1 <= len(request.correction_model or settings.ollama_correction_model) <= 200
            ))
            or request.source_language not in SUPPORTED_LANGUAGES
            or any(lang not in SUPPORTED_LANGUAGES for lang in request.target_languages)
            or settings.faster_whisper_device not in {"auto", "cpu"}
            or settings.faster_whisper_compute_type not in {"default", "int8"}
            or settings.faster_whisper_chunk_seconds != 4
            or settings.max_segments != 100
            or settings.vad_threshold_dbfs != -45 or settings.vad_hangover_frames != 3):
        raise ValueError(
            "Rust rollout requires source-mode CPU/int8 AUTO or faster-whisper, "
            "none or Ollama correction, and a configured translation provider; "
            "keep default VAD, 4s chunks and 100 live captions, or use the Python runtime"
        )
    validate_local_pairs(request)
    # Private credentials/options share the one bounded bootstrap, not a second
    # context copy. Reserve 8KiB for owned command/path/control metadata.
    snapshot = {"context": _source_context(request),
                "translation": _translation_options(settings, request)}
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > 56 * 1024:
        raise ValueError("Rust inference options exceed the bootstrap safety limit")
    binary = Path(settings.rust_runtime_binary).expanduser()
    if (not settings.rust_runtime_binary or not binary.is_file()
            or not os.access(binary, os.X_OK)):
        raise ValueError("configure a built Rust runtime binary before selecting Rust")


def _public_translation_model(model: str | None) -> str | None:
    return (None if isinstance(model, str) and Path(model).expanduser().is_dir()
            else public_model_identity(model))


def _translation_options(settings: Settings, request: StartSessionRequest) -> dict | None:
    options = provider_options(settings, request)
    translator = create_translator(options)
    if translator is None:
        return None
    private = {"provider": translator.provider, "model": translator.model,
               "url": options.get("url", ""), "api_key": options.get("api_key", ""),
               "timeout_seconds": options.get("timeout_seconds", 20.)}
    if (len(private["url"].encode()) > 4096 or len(private["api_key"].encode()) > 8192
            or not isinstance(private["model"], str) or not 1 <= len(private["model"]) <= 200):
        raise ValueError("invalid translation provider configuration")
    return {**private, "requested_model": _public_translation_model(request.translation_model),
            "configured_model": _public_translation_model(translator.model)}


class RustRuntimeBridge(AsrEngine):
    """One native runtime lease, private command pipe and authorized observer relay."""

    def __init__(self, publish, settings: Settings, slot: asyncio.Semaphore) -> None:
        super().__init__(publish)
        self.settings = settings
        self.slot = slot
        self.session_id: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._cleanup: asyncio.Task | None = None
        self._pending: asyncio.Task | None = None
        self._command_lock = asyncio.Lock()
        self._observed = asyncio.Condition()
        self._serial = 0
        self._sequence = 0
        self._running = False
        self._failure: str | None = None
        self._leased = False
        self._mt_slot = None
        self._ready = False
        self._configured = False
        self._stopped = False
        self._stopping = False
        self.persistence_error: str | None = None
        self.correction_status = CorrectionStatus()
        self.translation_status = TranslationStatus()
        self._metrics: RustMetricsFrame | None = None
        self._metrics_received_at: float | None = None
        self._sample_rate = 16_000
        self._queue_capacity = 8
        self._whisper_ready = True
        self._active_provider: str | None = None
        self._input_ended = False

    running = property(lambda self: self._running and not self._input_ended and not self._failure)
    accepts_audio = property(lambda self: True)
    sample_rate = property(lambda self: self._sample_rate)
    queue_capacity = property(lambda self: self._queue_capacity)
    active_provider = property(lambda self: self._active_provider)
    failover_count = property(lambda self: (
        self._metrics.source.failover_count if self._metrics is not None else None
    ) or 0)
    last_failover_reason = property(lambda self: (
        self._metrics.source.last_failover_reason if self._metrics is not None else None
    ))
    failure = property(lambda self: self._failure)

    def metrics_view(self) -> RustAuthorityMetrics:
        age = (None if self._metrics_received_at is None
               else max(0., (time.monotonic() - self._metrics_received_at) * 1000))
        if self._failure:
            status = "unavailable"
        elif self._metrics is None:
            status = "not_sampled"
        elif self._metrics.final:
            status = "unavailable" if self._metrics.stop_error else "final"
        else:
            status = "stale" if age is None or age > 1500 else "fresh"
        return RustAuthorityMetrics(status=status, age_ms=age, sample=self._metrics)

    def _accept_metrics(self, value: dict, size: int) -> RustMetricsFrame:
        if size > 4096:
            raise AsrEngineError(_ERROR)
        frame = RustMetricsFrame.model_validate(value)
        if frame.session_id != self.session_id or frame.session_generation != 1:
            raise AsrEngineError(_ERROR)
        if self._metrics is not None:
            if frame.sequence <= self._metrics.sequence:
                if frame.sequence == self._metrics.sequence and frame != self._metrics:
                    raise AsrEngineError(_ERROR)
                return frame  # Replay never refreshes the receipt clock.
            if self._metrics.final:
                raise AsrEngineError(_ERROR)
        self._metrics = frame
        self._metrics_received_at = time.monotonic()
        self._active_provider = frame.source.active_provider
        if frame.ingress.failure is not None and not self._stopping:
            self._failure = _ERROR
        return frame

    def _worker_commands(self, request: StartSessionRequest) -> tuple[dict, dict | None]:
        low = self.settings.resource_profile == "low" or request.resource_profile == "low"
        model = (self.settings.low_resource_whisper_model if low
                 else self.settings.faster_whisper_model)
        cache = str(managed_whisper_cache(self.settings.model_cache_dir))
        # Resolve before starting a process; this shared lookup is local-only.
        try:
            model_path = str(resolve_whisper_model(model, cache))
            self._whisper_ready = True
        except FileNotFoundError:
            if request.engine != "auto" or low:
                raise
            model_path = model
            self._whisper_ready = False
        asr = _python_worker("langtextflow.inference_worker", [
            model_path, cache,
            ("auto" if request.engine == "auto" and not low
             else str(self.settings.low_resource_cpu_threads)),
        ])
        mt = None
        if request.translation_provider == "m2m100-ct2":
            from .m2m100_cache import m2m100_cache_paths
            model_dir, tokenizer_dir = m2m100_cache_paths(self.settings)
            mt = _python_worker("langtextflow.translation.m2m100_worker", [
                "--rust-protocol",
                str(model_dir), str(tokenizer_dir),
                str(self.settings.low_resource_cpu_threads if low
                    else self.settings.local_m2m100_cpu_threads),
            ])
        elif request.translation_provider != "none":
            mt = _python_worker("langtextflow.translation_worker", [])
        return asr, mt

    def _auto_options(self, request: StartSessionRequest) -> dict | None:
        if request.engine != "auto":
            return None
        low = self.settings.resource_profile == "low" or request.resource_profile == "low"
        sidecar = None
        if not low:
            sidecar = {
                **_python_worker("langtextflow.asr_stream_worker", []),
                "endpoint": self.settings.vibevoice_url,
            }
        return {
            "sidecar": sidecar, "whisper_ready": self._whisper_ready,
            "low_resource": low,
            "replay_seconds": self.settings.asr_replay_seconds,
            "health_ms": max(1, round(self.settings.asr_health_check_seconds * 1000)),
            "startup_ms": max(1, round(self.settings.session_start_timeout_seconds * 1000)),
            "queue_frames": min(8, self.settings.audio_queue_chunks),
        }

    async def start(self, request: StartSessionRequest) -> None:
        validate_rust_request(self.settings, request)
        context = _source_context(request)
        translation_options = _translation_options(self.settings, request)
        self.translation_status = TranslationStatus(
            enabled=translation_options is not None, provider=request.translation_provider,
            model=translation_options["configured_model"] if translation_options else None,
        )
        correction_options = {
            "provider": request.correction_provider,
            "url": self.settings.ollama_url if request.correction_provider != "none" else "",
            "model": ((request.correction_model or self.settings.ollama_correction_model)
                      if request.correction_provider != "none" else "unused"),
            "timeout_seconds": max(self.settings.correction_timeout_seconds, .1),
        }
        self.correction_status = CorrectionStatus(
            enabled=request.correction_provider != "none", provider=request.correction_provider,
            model=(public_model_identity(correction_options["model"])
                   if request.correction_provider != "none" else None),
        )
        if not self.session_id:
            raise AsrEngineError(_ERROR)
        try:
            await asyncio.wait_for(self.slot.acquire(), timeout=0.05)
        except TimeoutError as exc:
            raise AsrEngineError(_ERROR) from exc
        self._leased = True
        try:
            if request.translation_provider == "m2m100-ct2":
                from .translation.m2m100_ct2 import _CHILD_SLOT

                if not _CHILD_SLOT.acquire(blocking=False):
                    raise AsrEngineError(_ERROR)
                self._mt_slot = _CHILD_SLOT
            asr, mt = await asyncio.to_thread(self._worker_commands, request)
            config = json.dumps({
                "protocol_version": 1, "session_id": self.session_id,
                "source_language": request.source_language,
                "target_languages": request.target_languages,
                "database_path": str(Path(self.settings.database_path).resolve()),
                "spool_directory": tempfile.gettempdir(), "asr": asr, "mt": mt,
                "translation": translation_options,
                "translation_prepare_ms": max(1, round(
                    self.settings.provider_prepare_timeout_seconds * 1000,
                )),
                "auto_asr": self._auto_options(request),
                "asr_startup_ms": max(1, round(self.settings.session_start_timeout_seconds * 1000)),
                "audio_queue_frames": min(8, self.settings.audio_queue_chunks),
                "asr_model_identity": self._configured_model(request),
                "context": context,
                "correction": {
                    "worker": _python_worker("langtextflow.correction_worker", []),
                    "options": {**correction_options, "public_model": self.correction_status.model},
                },
                "translation_deadline_ms": round(
                    self.settings.translation_target_timeout_seconds * 1000,
                ),
            }, ensure_ascii=False).encode() + b"\n"
            if len(config) > 64 * 1024:
                raise AsrEngineError(_ERROR)
            self._process = await asyncio.create_subprocess_exec(
                str(Path(self.settings.rust_runtime_binary).expanduser().resolve()),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=4096,
            )
            self._configured = True
            self._process.stdin.write(config)
            await self._process.stdin.drain()
            ready = await self._reply("ready", self.settings.session_start_timeout_seconds)
            if (ready.get("protocol_version") != 1
                    or type(ready.get("metrics_protocol_version")) is not int
                    or ready.get("metrics_protocol_version") != 1
                    or type(ready.get("sample_rate")) is not int
                    or not 8000 <= ready["sample_rate"] <= 192000
                    or type(ready.get("queue_capacity")) is not int
                    or not 1 <= ready["queue_capacity"] <= 8
                    or ready.get("active_provider") not in {"vibevoice", "faster-whisper"}
                    or type(ready.get("port")) is not int or not 1 <= ready["port"] <= 65535
                    or not isinstance(ready.get("observer_path"), str)
                    or not ready["observer_path"].startswith("/ws/observer?token=")
                    or len(ready["observer_path"]) != len("/ws/observer?token=") + 64
                    or any(c not in "0123456789abcdef" for c in ready["observer_path"][-64:])):
                raise AsrEngineError(_ERROR)
            self._ready = True
            self._sample_rate = ready["sample_rate"]
            self._queue_capacity = ready["queue_capacity"]
            self._active_provider = ready["active_provider"]
            self._translation_health(ready)
            self.correction_status.available = ready.get("correction_available") is True
            if self.correction_status.enabled and not self.correction_status.available:
                self.correction_status.error = "correction provider unavailable"
            if ready.get("history_available") is not True:
                self.persistence_error = "Rust history unavailable; archive may be incomplete."
            # Endpoint derives solely from a bounded integer port; no redirects,
            # ambient proxies, external URLs or credentials enter this route.
            socket = await connect(
                f"ws://127.0.0.1:{ready['port']}{ready['observer_path']}",
                origin=_ORIGIN, proxy=None, open_timeout=3, close_timeout=1,
                max_size=_MAX_FRAME, max_queue=8, write_limit=4096,
            )
            self._reader = asyncio.create_task(self._relay(socket), name="rust-caption-relay")
            self._running = True
        except asyncio.CancelledError:
            with suppress(Exception):
                await self.stop()
            raise
        except Exception as exc:
            await self.stop()
            raise AsrEngineError(_ERROR) from exc

    def _configured_model(self, request: StartSessionRequest) -> str | None:
        low = self.settings.resource_profile == "low" or request.resource_profile == "low"
        model = (self.settings.low_resource_whisper_model if low
                 else self.settings.faster_whisper_model)
        # Local directories take precedence in the shared resolver. A public
        # alias describes configuration only; verified checkpoint stays unknown.
        if not Path(model).expanduser().is_dir() and (
            model in WHISPER_REPOSITORIES or model in WHISPER_REPOSITORIES.values()
        ):
            return model
        return None

    def _translation_health(self, reply: dict) -> None:
        if type(reply.get("translation_available")) is not bool:
            raise AsrEngineError(_ERROR)
        self.translation_status.available = reply["translation_available"]
        self.translation_status.error = (
            "translation provider unavailable" if self.translation_status.enabled
            and not self.translation_status.available else None
        )

    async def _reply(self, kind: str, budget: float = _CONTROL_BUDGET) -> dict:
        if self._process is None:
            raise AsrEngineError(_ERROR)
        final_seen = False
        async with asyncio.timeout(budget):
            while True:
                raw = await self._process.stdout.readline()
                if not raw or len(raw) > 4096:
                    raise AsrEngineError(_ERROR)
                reply = json.loads(raw)
                if isinstance(reply, dict) and reply.get("type") == "metrics":
                    if kind != "stopped" or final_seen:
                        raise AsrEngineError(_ERROR)
                    frame = self._accept_metrics(reply, len(raw))
                    if not frame.final or frame != self._metrics:
                        raise AsrEngineError(_ERROR)
                    final_seen = True
                    continue
                if (len(raw) > 1024 or not isinstance(reply, dict) or reply.get("type") != kind
                        or reply.get("session_id") != self.session_id
                        or type(reply.get("session_generation")) is not int
                        or reply.get("session_generation") != 1
                        or (kind == "stopped" and not final_seen)):
                    raise AsrEngineError(_ERROR)
                return reply

    async def _relay(self, socket) -> None:
        try:
            async with socket:
                async for raw in socket:
                    frame = json.loads(raw)
                    if isinstance(frame, dict) and frame.get("type") == "metrics":
                        self._accept_metrics(frame, len(raw.encode()) + 1)
                        continue
                    if (frame.get("session_id") != self.session_id
                            or type(frame.get("session_generation")) is not int
                            or frame.get("session_generation") != 1
                            or type(frame.get("serial")) is not int
                            or not 0 <= frame["serial"] < 2**53
                            or frame["serial"] < self._serial):
                        raise AsrEngineError(_ERROR)
                    if frame.get("type") == "transcript":
                        event = TranscriptEvent.model_validate(frame)
                        self.translation_status.errors_by_language = {
                            target: "translation timed out" if state == "timed_out"
                            else "translation provider failed"
                            for target, state in event.translation_states.items()
                            if state in {"failed", "timed_out"}
                        }
                        if event.correction is not None:
                            if event.correction.llm_attempted:
                                self.correction_status.available = True
                            self.correction_status.error = event.correction.fallback_reason
                        await self.publish(event)
                    elif frame.get("type") == "snapshot":
                        # No source revisions are accepted from this audio route;
                        # initial snapshot is empty. Reconnect never invents events.
                        if frame.get("segments"):
                            raise AsrEngineError(_ERROR)
                    else:
                        raise AsrEngineError(_ERROR)
                    async with self._observed:
                        self._serial = frame["serial"]
                        self._observed.notify_all()
        except Exception:
            if not self._stopping:
                self._failure = _ERROR
        finally:
            self._running = False
            if not self._stopping:
                self._failure = _ERROR
                if self._process is not None:
                    self._process.stdin.close()

    async def _command(self, tag: int, frame: bytes = b"") -> None:
        if self._process is None or self._failure or self._process.returncode is not None:
            raise AsrEngineError(_ERROR)
        async with self._command_lock:
            if self._pending is not None:
                await asyncio.shield(self._pending)
            # Once pipe bytes are written, the exchange has one owned ACK reader.
            # A browser disconnect cannot cancel it and corrupt the next command.
            self._pending = asyncio.create_task(self._exchange(tag, frame))
            self._pending.add_done_callback(
                lambda task: None if task.cancelled() else task.exception(),
            )
            await asyncio.shield(self._pending)

    async def _exchange(self, tag: int, frame: bytes) -> None:
        try:
            self._sequence += 1
            self._process.stdin.write(struct.pack("<BI", tag, len(frame)) + frame)
            budget = _CONTROL_BUDGET + max(self.settings.translation_target_timeout_seconds,
                                          self.settings.correction_timeout_seconds)
            async with asyncio.timeout(budget):
                await self._process.stdin.drain()
                reply = await self._reply("ack", budget)
                self._translation_health(reply)
                if (type(reply.get("sequence")) is not int
                        or reply.get("sequence") != self._sequence
                        or type(reply.get("serial")) is not int
                        or not 0 <= reply["serial"] < 2**53):
                    raise AsrEngineError(_ERROR)
                if not reply.get("history") or reply["history"].get("complete") is not True:
                    self.persistence_error = "Rust history incomplete; inspect local storage."
                async with self._observed:
                    await self._observed.wait_for(lambda: self._serial >= reply["serial"])
                if tag in {2, 3}:
                    self._input_ended = True
        except (Exception, asyncio.CancelledError):
            self._failure = _ERROR
            if self._process.stdin is not None:
                self._process.stdin.close()
            raise

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if not self.running or not 0 < len(pcm_f32le) <= _MAX_FRAME or len(pcm_f32le) % 4:
            raise AsrEngineError(_ERROR)
        await self._command(1, pcm_f32le)

    async def end_audio(self) -> None:
        await self._command(2)

    async def stop(self) -> None:
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._finish(), name="rust-runtime-stop-owner")
            self._cleanup.add_done_callback(
                lambda task: None if task.cancelled() else task.exception(),
            )
        await asyncio.shield(self._cleanup)

    async def _finish(self) -> None:
        process = self._process
        self._stopping = True
        try:
            if process is not None:
                if self._ready and not self._failure and process.returncode is None:
                    try:
                        await self._command(3)
                        stopped = await self._reply("stopped")
                        self._stopped = stopped.get("error") is None
                        history = stopped.get("history")
                        if not history or history.get("complete") is not True:
                            self.persistence_error = (
                                "Rust history incomplete; inspect local storage."
                            )
                    except Exception:
                        self._failure = _ERROR
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 8)
                except TimeoutError:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 3)
                    self._failure = _ERROR
                if process.returncode != 0:
                    self._failure = _ERROR
                else:
                    # The shipped host exits zero after confirmed owned cleanup,
                    # including EOF cancellation, not merely after its parent exits.
                    self._stopped = True
        finally:
            self._running = False
            if self._reader is not None:
                self._reader.cancel()
                with suppress(asyncio.CancelledError):
                    await self._reader
            # A crashed/forcibly killed authority cannot confirm descendant reap.
            # Keep its shared lease quarantined instead of oversubscribing native workers.
            if self._leased and (not self._configured or self._stopped):
                self._leased = False
                self.slot.release()
                if self._mt_slot is not None:
                    self._mt_slot.release()
                    self._mt_slot = None
        if self._failure:
            raise AsrEngineError(_ERROR)
