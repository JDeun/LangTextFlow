from __future__ import annotations

import pytest

from langtextflow.asr.base import AsrEngine, AsrEngineError
from langtextflow.asr.fallback import StartupFallbackAsrEngine
from langtextflow.models import StartSessionRequest


class FakeEngine(AsrEngine):
    def __init__(self, publish, *, fail: bool = False, queue_depth: int = 0) -> None:
        super().__init__(publish)
        self.fail = fail
        self.started = False
        self.stopped = False
        self.frames: list[bytes] = []
        self.ended = False
        self._queue_depth = queue_depth

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    @property
    def accepts_audio(self) -> bool:
        return True

    @property
    def sample_rate(self) -> int:
        return 16000

    @property
    def queue_depth(self) -> int:
        return self._queue_depth

    @property
    def queue_capacity(self) -> int:
        return 8

    async def start(self, request: StartSessionRequest) -> None:
        del request
        if self.fail:
            raise AsrEngineError("startup failed")
        self.started = True
        self.stopped = False

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        self.frames.append(pcm_f32le)

    async def end_audio(self) -> None:
        self.ended = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_startup_fallback_uses_second_provider_after_first_failure() -> None:
    async def publish(event) -> None:
        del event

    primary = FakeEngine(publish, fail=True)
    fallback = FakeEngine(publish, queue_depth=3)
    engine = StartupFallbackAsrEngine(
        publish,
        [("primary", primary), ("fallback", fallback)],
    )

    await engine.start(StartSessionRequest(engine="auto"))
    assert engine.active_provider == "fallback"
    assert engine.running is True
    assert engine.queue_depth == 3
    assert engine.queue_capacity == 8
    assert primary.stopped is True

    frame = b"\x00\x00\x00\x00"
    await engine.feed_audio(frame)
    assert fallback.frames == [frame]

    await engine.end_audio()
    assert fallback.ended is True
    await engine.stop()
    assert fallback.stopped is True
    assert engine.active_provider is None


@pytest.mark.asyncio
async def test_startup_fallback_reports_all_provider_failures() -> None:
    async def publish(event) -> None:
        del event

    first = FakeEngine(publish, fail=True)
    second = FakeEngine(publish, fail=True)
    engine = StartupFallbackAsrEngine(
        publish,
        [("vibevoice", first), ("faster-whisper", second)],
    )

    with pytest.raises(AsrEngineError) as exc_info:
        await engine.start(StartSessionRequest(engine="auto"))

    message = str(exc_info.value)
    assert "vibevoice" in message
    assert "faster-whisper" in message
