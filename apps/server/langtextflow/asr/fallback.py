from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress

from langtextflow.asr.base import AsrEngine, AsrEngineError, PublishEvent
from langtextflow.models import StartSessionRequest, TranscriptEvent

ProviderFactory = Callable[[PublishEvent], AsrEngine]
ProviderChange = Callable[[str], Awaitable[None]]


def _strip_exact_text_overlap(previous: str, current: str, *, minimum: int = 4) -> str:
    """Remove an exact suffix/prefix overlap while preserving the current wording.

    This is intentionally conservative. Approximate/fuzzy dedupe can erase real
    speech after a provider handoff, so only exact case-insensitive overlap of at
    least `minimum` characters is removed.
    """

    left = previous.strip()
    right = current.strip()
    if not left or not right:
        return right
    max_overlap = min(len(left), len(right), 240)
    for size in range(max_overlap, minimum - 1, -1):
        if left[-size:].casefold() == right[:size].casefold():
            return right[size:].lstrip(" \t\n,.;:!?…-—")
    return right


class StartupFallbackAsrEngine(AsrEngine):
    """Try ASR providers in order and keep the first one that starts successfully."""

    def __init__(
        self,
        publish: PublishEvent,
        candidates: Sequence[tuple[str, AsrEngine]],
    ) -> None:
        super().__init__(publish)
        if not candidates:
            raise ValueError("at least one ASR fallback candidate is required")
        self.candidates = list(candidates)
        self._active_name: str | None = None
        self._active: AsrEngine | None = None

    @property
    def active_provider(self) -> str | None:
        return self._active_name

    @property
    def running(self) -> bool:
        return self._active.running if self._active is not None else False

    @property
    def accepts_audio(self) -> bool:
        if self._active is not None:
            return self._active.accepts_audio
        return all(engine.accepts_audio for _, engine in self.candidates)

    @property
    def sample_rate(self) -> int:
        if self._active is not None:
            return self._active.sample_rate
        return self.candidates[0][1].sample_rate

    @property
    def queue_depth(self) -> int:
        return self._active.queue_depth if self._active is not None else 0

    @property
    def queue_capacity(self) -> int:
        return self._active.queue_capacity if self._active is not None else 0

    @property
    def failure(self) -> str | None:
        return self._active.failure if self._active is not None else None

    async def start(self, request: StartSessionRequest) -> None:
        if self._active is not None and self._active.running:
            return
        failures: list[str] = []
        for name, engine in self.candidates:
            try:
                await engine.start(request)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                with suppress(Exception):
                    await engine.stop()
                continue
            self._active_name = name
            self._active = engine
            return
        details = "; ".join(failures) or "no candidates were attempted"
        raise AsrEngineError(f"all ASR providers failed to start: {details}")

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self._active is None:
            raise AsrEngineError("no ASR provider is active")
        await self._active.feed_audio(pcm_f32le)

    async def end_audio(self) -> None:
        if self._active is not None:
            await self._active.end_audio()

    async def stop(self) -> None:
        if self._active is not None:
            await self._active.stop()
        self._active = None
        self._active_name = None


class ReplayFallbackAsrEngine(AsrEngine):
    """ASR provider chain with replay-assisted mid-session handoff.

    The wrapper owns a bounded PCM ring buffer. If the active provider fails, the
    next provider is started, recent audio is replayed, and provider-local event
    timestamps are rebased onto the session timeline. Exact text overlap and
    events fully covered by the previous provider are suppressed conservatively.

    Failover is ordered and one-way: a failed provider is not retried during the
    same session. This avoids oscillation and duplicate output storms.
    """

    def __init__(
        self,
        publish: PublishEvent,
        candidates: Sequence[tuple[str, ProviderFactory]],
        *,
        replay_seconds: float = 8.0,
        health_check_seconds: float = 0.25,
        on_provider_change: ProviderChange | None = None,
    ) -> None:
        super().__init__(publish)
        if not candidates:
            raise ValueError("at least one ASR fallback candidate is required")
        if replay_seconds <= 0:
            raise ValueError("replay_seconds must be greater than zero")
        if health_check_seconds <= 0:
            raise ValueError("health_check_seconds must be greater than zero")
        self.candidates = list(candidates)
        self.replay_seconds = replay_seconds
        self.health_check_seconds = health_check_seconds
        self.on_provider_change = on_provider_change
        self._request: StartSessionRequest | None = None
        self._active_name: str | None = None
        self._active_index = -1
        self._active: AsrEngine | None = None
        self._sample_rate = 16000
        self._generation = 0
        self._provider_origin_ms = 0.0
        self._pending_generation: int | None = None
        self._pending_events: list[TranscriptEvent] = []
        self._audio_frames: deque[bytes] = deque()
        self._buffer_bytes = 0
        self._audio_cursor_ms = 0.0
        self._last_published_end_ms = 0
        self._recent_text = ""
        self._failure: str | None = None
        self._failover_count = 0
        self._last_failover_reason: str | None = None
        self._ended = False
        self._lock = asyncio.Lock()
        self._health_task: asyncio.Task[None] | None = None

    @property
    def active_provider(self) -> str | None:
        return self._active_name

    @property
    def running(self) -> bool:
        return (
            not self._ended
            and self._failure is None
            and self._active is not None
            and self._active.running
        )

    @property
    def accepts_audio(self) -> bool:
        return True

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def queue_depth(self) -> int:
        return self._active.queue_depth if self._active is not None else 0

    @property
    def queue_capacity(self) -> int:
        return self._active.queue_capacity if self._active is not None else 0

    @property
    def failure(self) -> str | None:
        if self._failure is not None:
            return self._failure
        return self._active.failure if self._active is not None else None

    @property
    def failover_count(self) -> int:
        return self._failover_count

    @property
    def last_failover_reason(self) -> str | None:
        return self._last_failover_reason

    async def start(self, request: StartSessionRequest) -> None:
        if self.running:
            return
        self._request = request
        self._failure = None
        self._ended = False
        self._generation = 0
        self._provider_origin_ms = 0.0
        self._pending_generation = None
        self._pending_events.clear()
        self._audio_frames.clear()
        self._buffer_bytes = 0
        self._audio_cursor_ms = 0.0
        self._last_published_end_ms = 0
        self._recent_text = ""
        self._failover_count = 0
        self._last_failover_reason = None

        failures: list[str] = []
        for index in range(len(self.candidates)):
            try:
                engine = await self._start_candidate(index, generation=0)
            except Exception as exc:
                failures.append(f"{self.candidates[index][0]}: {exc}")
                continue
            self._activate_candidate(index, engine, generation=0, origin_ms=0.0)
            await self._notify_provider_change()
            await self._flush_pending_events()
            self._health_task = asyncio.create_task(
                self._health_loop(),
                name="asr-provider-health-monitor",
            )
            return

        details = "; ".join(failures) or "no candidates were attempted"
        self._failure = f"all ASR providers failed to start: {details}"
        raise AsrEngineError(self._failure)

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self._ended:
            raise AsrEngineError("ASR audio stream has already ended")
        if self._failure is not None:
            raise AsrEngineError(self._failure)
        if not pcm_f32le or len(pcm_f32le) % 4:
            raise ValueError("audio frame must contain little-endian float32 PCM")

        self._remember_audio(pcm_f32le)
        async with self._lock:
            active = self._active
            if active is None:
                raise AsrEngineError("no ASR provider is active")
            if active.failure is not None or not active.running:
                reason = active.failure or f"{self._active_name or 'ASR provider'} stopped"
                await self._failover_locked(reason)
                return
            try:
                await active.feed_audio(pcm_f32le)
            except Exception as exc:
                reason = active.failure or str(exc)
                await self._failover_locked(reason)

    async def end_audio(self) -> None:
        if self._ended:
            return
        self._ended = True
        await self._cancel_health_task()
        if self._active is not None:
            await self._active.end_audio()

    async def stop(self) -> None:
        self._ended = True
        await self._cancel_health_task()
        if self._active is not None:
            with suppress(Exception):
                await self._active.stop()
        self._active = None
        self._active_name = None
        self._active_index = -1
        self._request = None
        self._pending_generation = None
        self._pending_events.clear()
        self._audio_frames.clear()
        self._buffer_bytes = 0

    async def _cancel_health_task(self) -> None:
        task = self._health_task
        self._health_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _health_loop(self) -> None:
        try:
            while not self._ended:
                await asyncio.sleep(self.health_check_seconds)
                active = self._active
                if active is None or active.failure is None:
                    continue
                async with self._lock:
                    if active is self._active and active.failure is not None and not self._ended:
                        try:
                            await self._failover_locked(active.failure)
                        except AsrEngineError:
                            return
        except asyncio.CancelledError:
            raise

    async def _start_candidate(self, index: int, *, generation: int) -> AsrEngine:
        if self._request is None:
            raise AsrEngineError("ASR fallback session is not initialized")
        name, factory = self.candidates[index]
        self._pending_generation = generation
        self._pending_events.clear()

        async def provider_publish(event: TranscriptEvent) -> None:
            await self._publish_from_provider(generation, event)

        engine = factory(provider_publish)
        try:
            await engine.start(self._request)
        except Exception:
            self._pending_generation = None
            self._pending_events.clear()
            with suppress(Exception):
                await engine.stop()
            raise
        if index > 0 and engine.sample_rate != self._sample_rate:
            self._pending_generation = None
            self._pending_events.clear()
            with suppress(Exception):
                await engine.stop()
            raise AsrEngineError(
                f"{name} sample rate {engine.sample_rate} does not match active stream "
                f"{self._sample_rate}"
            )
        return engine

    def _activate_candidate(
        self,
        index: int,
        engine: AsrEngine,
        *,
        generation: int,
        origin_ms: float,
    ) -> None:
        self._active_index = index
        self._active_name = self.candidates[index][0]
        self._active = engine
        self._generation = generation
        self._provider_origin_ms = origin_ms
        if index == 0 and self._audio_cursor_ms == 0:
            self._sample_rate = engine.sample_rate
        self._failure = None
        self._pending_generation = None

    async def _notify_provider_change(self) -> None:
        if self.on_provider_change is not None and self._active_name is not None:
            await self.on_provider_change(self._active_name)

    async def _failover_locked(self, reason: str) -> None:
        if self._request is None:
            raise AsrEngineError("ASR fallback session is not initialized")
        old_engine = self._active
        old_name = self._active_name or "unknown"
        if old_engine is not None:
            with suppress(Exception):
                await old_engine.stop()

        replay_frames = tuple(self._audio_frames)
        replay_duration_ms = self._buffer_bytes / 4 / self._sample_rate * 1000.0
        origin_ms = max(0.0, self._audio_cursor_ms - replay_duration_ms)
        generation = self._generation + 1
        failures: list[str] = []

        for index in range(self._active_index + 1, len(self.candidates)):
            name = self.candidates[index][0]
            engine: AsrEngine | None = None
            try:
                engine = await self._start_candidate(index, generation=generation)
                for frame in replay_frames:
                    await engine.feed_audio(frame)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                if engine is not None:
                    with suppress(Exception):
                        await engine.stop()
                self._pending_generation = None
                self._pending_events.clear()
                continue

            self._activate_candidate(
                index,
                engine,
                generation=generation,
                origin_ms=origin_ms,
            )
            self._failover_count += 1
            self._last_failover_reason = f"{old_name}: {reason}"
            await self._notify_provider_change()
            await self._flush_pending_events()
            return

        detail = "; ".join(failures) if failures else "no fallback providers remain"
        self._active = None
        self._active_name = old_name
        self._failure = f"ASR failover exhausted after {old_name} failed: {reason}; {detail}"
        raise AsrEngineError(self._failure)

    def _remember_audio(self, pcm_f32le: bytes) -> None:
        self._audio_frames.append(pcm_f32le)
        self._buffer_bytes += len(pcm_f32le)
        self._audio_cursor_ms += len(pcm_f32le) / 4 / self._sample_rate * 1000.0
        max_bytes = max(4, int(self.replay_seconds * self._sample_rate * 4))
        while self._buffer_bytes > max_bytes and len(self._audio_frames) > 1:
            removed = self._audio_frames.popleft()
            self._buffer_bytes -= len(removed)

    async def _publish_from_provider(self, generation: int, event: TranscriptEvent) -> None:
        if generation == self._pending_generation:
            self._pending_events.append(event)
            return
        if generation != self._generation or self._active is None:
            return
        await self._emit_rebased(event)

    async def _flush_pending_events(self) -> None:
        events = list(self._pending_events)
        self._pending_events.clear()
        self._pending_generation = None
        for event in events:
            await self._emit_rebased(event)

    async def _emit_rebased(self, event: TranscriptEvent) -> None:
        mapped_start = max(0, int(round(self._provider_origin_ms + event.start_ms)))
        mapped_end = (
            max(mapped_start, int(round(self._provider_origin_ms + event.end_ms)))
            if event.end_ms is not None
            else None
        )
        text = event.text.strip()

        if self._generation > 0 and mapped_end is not None:
            if mapped_end <= self._last_published_end_ms:
                return
            if mapped_start < self._last_published_end_ms:
                text = _strip_exact_text_overlap(self._recent_text, text)
                if not text:
                    return
                mapped_start = self._last_published_end_ms

        rebased = event.model_copy(
            update={
                "segment_id": f"auto-{self._generation:02d}-{event.segment_id}",
                "text": text,
                "start_ms": mapped_start,
                "end_ms": mapped_end,
            }
        )
        await self.publish(rebased)

        if mapped_end is not None:
            self._last_published_end_ms = max(self._last_published_end_ms, mapped_end)
        if text:
            self._recent_text = f"{self._recent_text} {text}".strip()[-1000:]
