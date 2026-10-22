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
    await engine.start(
        StartSessionRequest(engine="auto", source_language="en", target_languages=["ko"])
    )
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



@pytest.mark.asyncio
async def test_replay_fallback_fences_old_session_callbacks_across_restart() -> None:
    published: list[TranscriptEvent] = []
    created: list[FakeEngine] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    def factory(provider_publish):
        new = FakeEngine(provider_publish)
        created.append(new)
        return new

    wrapper = ReplayFallbackAsrEngine(
        publish, [("primary", factory)], health_check_seconds=60.0,
    )
    request = StartSessionRequest(
        engine="auto", source_language="en", target_languages=["ko"],
    )
    await wrapper.start(request)
    original_epoch = wrapper._session_epoch
    old = created[-1]
    await old.emit(segment_id="before-stop", start_ms=0, end_ms=300, text="old")
    assert len(published) == 1

    await wrapper.stop()
    await old.emit(segment_id="after-stop", start_ms=350, end_ms=500, text="stale")
    assert len(published) == 1

    await wrapper.start(request)
    assert wrapper._session_epoch > original_epoch
    new = created[-1]
    assert new is not old
    # Reused transcript generation=0 must not reuse the closed session epoch.
    await old.emit(segment_id="old-late", start_ms=0, end_ms=300, text="stale")
    await new.emit(segment_id="current", start_ms=0, end_ms=300, text="valid")
    assert [event.text for event in published] == ["old", "valid"]
    await wrapper.stop()


@pytest.mark.asyncio
async def test_replay_fallback_stop_does_not_wait_for_stuck_audio_lock() -> None:
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    class StalledEngine(FakeEngine):
        async def feed_audio(self, pcm_f32le: bytes) -> None:
            started.set()
            await release.wait()
            await super().feed_audio(pcm_f32le)

    async def publish(_event: TranscriptEvent) -> None:
        pass

    wrapper = ReplayFallbackAsrEngine(
        publish, [("stalled", lambda sink: StalledEngine(sink))],
        health_check_seconds=60.0,
    )
    await wrapper.start(StartSessionRequest(engine="auto"))
    in_flight = asyncio.create_task(wrapper.feed_audio(pcm(20, 0.1)))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        # Provider callback fencing must not add an unbounded wait for _lock.
        await asyncio.wait_for(wrapper.stop(), timeout=0.2)
        assert wrapper.active_provider is None
    finally:
        release.set()
        await asyncio.gather(in_flight, return_exceptions=True)



@pytest.mark.asyncio
async def test_failed_asr_candidate_cannot_inject_callbacks_during_next_start() -> None:
    published: list[TranscriptEvent] = []
    failed: list[FakeEngine] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    class FailingStart(FakeEngine):
        async def start(self, request: StartSessionRequest) -> None:
            await super().start(request)
            raise AsrEngineError("first candidate startup failed")

    class HealthyStart(FakeEngine):
        async def start(self, request: StartSessionRequest) -> None:
            await super().start(request)
            # Both callbacks have the same session and failover generation,
            # but only the currently starting candidate may enter the buffer.
            await failed[-1].emit(
                segment_id="stale", start_ms=0, end_ms=100, text="old candidate",
            )
            await self.emit(
                segment_id="current", start_ms=0, end_ms=100, text="healthy",
            )

    def build_failing(publish_event):
        engine = FailingStart(publish_event)
        failed.append(engine)
        return engine

    wrapper = ReplayFallbackAsrEngine(
        publish, [("bad", build_failing), ("good", HealthyStart)],
        health_check_seconds=60.0,
    )
    try:
        await wrapper.start(StartSessionRequest(engine="auto"))
        assert wrapper.active_provider == "good"
        assert [x.text for x in published] == ["healthy"]
    finally:
        await wrapper.stop()


@pytest.mark.asyncio
async def test_flooding_startup_candidate_is_bounded_and_rejected() -> None:
    published: list[TranscriptEvent] = []
    bad: list[FakeEngine] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    class FloodingStart(FakeEngine):
        async def start(self, request: StartSessionRequest) -> None:
            await super().start(request)
            for index in range(200):
                await self.emit(
                    segment_id=f"flood-{index}", start_ms=index,
                    end_ms=index + 1, text="untrusted startup",
                )

    def make_flood(publish_event):
        engine = FloodingStart(publish_event)
        bad.append(engine)
        return engine

    wrapper = ReplayFallbackAsrEngine(
        publish, [("flood", make_flood), ("good", FakeEngine)],
        health_check_seconds=60.0,
    )
    try:
        await wrapper.start(StartSessionRequest(engine="auto"))
        assert wrapper.active_provider == "good"
        assert bad[0].stopped is True
        assert published == []
        assert wrapper._pending_events == []
        assert wrapper._pending_overflowed is False
    finally:
        await wrapper.stop()


def test_replay_buffer_strictly_bounds_single_oversized_frame_and_keeps_true_clock() -> None:
    async def publish(_event: TranscriptEvent) -> None:
        pass

    engine = ReplayFallbackAsrEngine(
        publish, [("fake", lambda sink: FakeEngine(sink))],
        replay_seconds=0.01,
    )
    oversized = pcm(2500, 0.5)  # 156.25 ms vs a 10 ms replay budget.
    engine._remember_audio(oversized)
    budget = int(0.01 * 16000 * 4)
    assert engine._buffer_bytes == budget
    assert b"".join(engine._audio_frames) == oversized[-budget:]
    assert engine._audio_cursor_ms == pytest.approx(156.25)

    next_frame = pcm(32, 0.3)
    engine._remember_audio(next_frame)
    assert engine._buffer_bytes <= budget
    assert b"".join(engine._audio_frames).endswith(next_frame)
    assert engine._audio_cursor_ms == pytest.approx(158.25)
    assert all(len(frame) % 4 == 0 for frame in engine._audio_frames)


@pytest.mark.asyncio
async def test_failover_fences_outgoing_callbacks_before_stop_yields() -> None:
    """Late transcripts emitted by stop() must not escape the old provider."""
    published: list[TranscriptEvent] = []
    outgoing: list[FakeEngine] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    class EmitsWhileStopping(FakeEngine):
        async def stop(self) -> None:
            await self.emit(
                segment_id="late-stop", start_ms=0, end_ms=200, text="stale stop callback",
            )
            await super().stop()

    def primary_factory(sink):
        engine = EmitsWhileStopping(sink)
        outgoing.append(engine)
        return engine

    wrapper = ReplayFallbackAsrEngine(
        publish, [("primary", primary_factory), ("fallback", FakeEngine)],
        health_check_seconds=60.0,
    )
    try:
        await wrapper.start(StartSessionRequest(engine="auto"))
        await wrapper._failover_locked("forced failure")
        assert wrapper.active_provider == "fallback"
        assert published == []
    finally:
        await wrapper.stop()
