from __future__ import annotations

from collections.abc import Sequence

from langtextflow.asr.base import AsrEngine, AsrEngineError, PublishEvent
from langtextflow.models import StartSessionRequest


class StartupFallbackAsrEngine(AsrEngine):
    """Try ASR providers in order and keep the first one that starts successfully.

    Fallback is deliberately limited to startup. Mid-session provider failover
    requires replayable audio buffering and explicit duplicate suppression, so it
    is handled as a separate reliability milestone.
    """

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

    async def start(self, request: StartSessionRequest) -> None:
        if self._active is not None and self._active.running:
            return
        failures: list[str] = []
        for name, engine in self.candidates:
            try:
                await engine.start(request)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                try:
                    await engine.stop()
                except Exception:
                    pass
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
