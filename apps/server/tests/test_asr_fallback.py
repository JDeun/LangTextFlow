from __future__ import annotations

from array import array

import pytest

from langtextflow.asr.base import AsrEngine, AsrEngineError
from langtextflow.asr.fallback import ReplayFallbackAsrEngine, StartupFallbackAsrEngine
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent


class FakeEngine(AsrEngine):
    def __init__(
        self,
        publish,
        *,
        fail: bool = False,
        queue_depth: int = 0,
        failure: str | None = None,
        sample_rate: int = 16000,
    ) -> None:
        super().__init__(publish)
        self.fail = fail
        self.started = False
        self.stopped = False
        self.frames: list[bytes] = []
        self.ended = False
        self._queue_depth = queue_depth
        self._failure = failure
        self._sample_rate = sample_rate

    @property
    def running(self) -> bool:
        return self.started and not self.stopped and self._failure is None

    @property
    def accepts_audio(self) -> bool:
        return True

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def queue_depth(self) -> int:
        return self._queue_depth

    @property
    def queue_capacity(self) -> int:
        return 8

    @property
    def failure(self) -> str | None:
        return self._failure

    async def start(self, request: StartSessionRequest) -> None:
        del request
        if self.fail:
            raise AsrEngineError("startup failed")
        self.started = True
        self.stopped = False

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self._failure is not None:
            raise AsrEngineError(self._failure)
        self.frames.append(pcm_f32le)

    async def end_audio(self) -> None:
        self.ended = True

    async def stop(self) -> None:
        self.stopped = True

    async def emit(self, *, segment_id: str, start_ms: int, end_ms: int, text: str) -> None:
        await self.publish(
            TranscriptEvent(
                segment_id=segment_id,
                version=1,
                stage=CaptionStage.STABLE,
                source_language="en",
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )


def pcm(samples: int, value: float) -> bytes:
    return array("f", [value] * samples).tobytes()


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
    assert engine.failure is None
    assert engine.queue_depth == 3
    assert engine.queue_capacity == 8
    assert primary.stopped is True

    frame = b"\x00\x00\x00\x00"
    await engine.feed_audio(frame)
    assert fallback.frames == [frame]

    fallback._failure = "provider disconnected"
    assert engine.running is False
    assert engine.failure == "provider disconnected"

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


@pytest.mark.asyncio
async def test_replay_fallback_replays_recent_audio_rebases_time_and_suppresses_overlap() -> None:
    published: list[TranscriptEvent] = []
    provider_changes: list[str] = []
    created: dict[str, FakeEngine] = {}

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    async def provider_changed(name: str) -> None:
        provider_changes.append(name)

    def factory(name: str):
        def build(provider_publish):
            engine = FakeEngine(provider_publish, sample_rate=10)
            created[name] = engine
            return engine

        return build

    engine = ReplayFallbackAsrEngine(
        publish,
        [("primary", factory("primary")), ("fallback", factory("fallback"))],
        replay_seconds=1.0,
        health_check_seconds=60.0,
        on_provider_change=provider_changed,
    )
    await engine.start(StartSessionRequest(engine="auto", source_language="en"))
    assert engine.active_provider == "primary"
    assert provider_changes == ["primary"]

    first = pcm(5, 0.1)  # 500 ms at 10 Hz
    second = pcm(5, 0.2)
    current = pcm(5, 0.3)
    await engine.feed_audio(first)
    await engine.feed_audio(second)
    await created["primary"].emit(
        segment_id="seg-1",
        start_ms=0,
        end_ms=1000,
        text="hello world",
    )

    created["primary"]._failure = "socket lost"
    await engine.feed_audio(current)

    assert engine.active_provider == "fallback"
    assert engine.failover_count == 1
    assert engine.last_failover_reason == "primary: socket lost"
    assert provider_changes == ["primary", "fallback"]
    # The 1-second ring contains the second old frame plus the current frame.
    assert created["fallback"].frames == [second, current]

    # Fully replay-covered output is discarded.
    await created["fallback"].emit(
        segment_id="replay-old",
        start_ms=0,
        end_ms=500,
        text="hello world",
    )
    assert len(published) == 1

    # A straddling event is rebased onto the global clock and exact text overlap is removed.
    await created["fallback"].emit(
        segment_id="replay-new",
        start_ms=0,
        end_ms=1000,
        text="hello world again",
    )
    assert len(published) == 2
    assert published[1].segment_id == "auto-01-replay-new"
    assert published[1].start_ms == 1000
    assert published[1].end_ms == 1500
    assert published[1].text == "again"

    await engine.stop()


@pytest.mark.asyncio
async def test_replay_fallback_reports_exhaustion_after_last_provider_fails() -> None:
    created: dict[str, FakeEngine] = {}

    async def publish(event) -> None:
        del event

    def build(provider_publish):
        engine = FakeEngine(provider_publish, sample_rate=10)
        created["only"] = engine
        return engine

    engine = ReplayFallbackAsrEngine(
        publish,
        [("only", build)],
        replay_seconds=1.0,
        health_check_seconds=60.0,
    )
    await engine.start(StartSessionRequest(engine="auto"))
    created["only"]._failure = "device lost"

    with pytest.raises(AsrEngineError, match="failover exhausted"):
        await engine.feed_audio(pcm(1, 0.1))
    assert engine.running is False
    assert engine.failure is not None
    assert "device lost" in engine.failure

    await engine.stop()
