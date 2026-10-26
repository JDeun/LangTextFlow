"""Runtime-level ASR failover: replay fallback to live captions and SQLite."""
from __future__ import annotations

import asyncio
from array import array

import pytest

from langtextflow.asr.base import AsrEngine, AsrEngineError
from langtextflow.asr.fallback import ReplayFallbackAsrEngine
from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


class StubAsr(AsrEngine):
    def __init__(self, publish) -> None:
        super().__init__(publish)
        self.frames: list[bytes] = []
        self._failure: str | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and self._failure is None

    @property
    def accepts_audio(self) -> bool:
        return True

    @property
    def sample_rate(self) -> int:
        return 10

    @property
    def queue_depth(self) -> int:
        return 0

    @property
    def queue_capacity(self) -> int:
        return 8

    @property
    def failure(self) -> str | None:
        return self._failure

    async def start(self, request: StartSessionRequest) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self._failure:
            raise AsrEngineError(self._failure)
        self.frames.append(pcm_f32le)

    async def end_audio(self) -> None:
        pass

    async def emit(self, segment_id: str, text: str) -> None:
        await self.publish(TranscriptEvent(
            segment_id=segment_id, version=1, stage=CaptionStage.STABLE,
            source_language="ko", text=text, start_ms=1000, end_ms=1500,
        ))


class FailoverRuntime(CaptionRuntime):
    def _build_engine(self, request: StartSessionRequest) -> AsrEngine:
        self.providers: dict[str, StubAsr] = {}

        def factory(name: str):
            def create(publish):
                provider = StubAsr(publish)
                self.providers[name] = provider
                return provider
            return create

        return ReplayFallbackAsrEngine(
            self.pipeline.ingest,
            [("primary", factory("primary")), ("fallback", factory("fallback"))],
            replay_seconds=1.0,
            health_check_seconds=60.0,
            on_provider_change=self._provider_changed,
        )


@pytest.mark.asyncio
async def test_runtime_asr_failover_preserves_translation_and_durable_history(tmp_path) -> None:
    runtime = FailoverRuntime(Settings(database_path=str(tmp_path / "failover.sqlite3")))
    state = await runtime.start(StartSessionRequest(
        engine="auto", source_language="ko", target_languages=["en"],
        translation_provider="demo",
    ))
    assert state.session_id is not None
    try:
        frame = array("f", [0.1] * 5).tobytes()
        await runtime.feed_audio(frame)
        runtime.providers["primary"]._failure = "primary disconnected"
        await runtime.feed_audio(frame)
        assert runtime.engine.active_provider == "fallback"
        assert runtime.metrics.asr_failover_count == 1
        assert runtime.providers["fallback"].frames
        await runtime.providers["fallback"].emit("after-failover", "복음에 관하여")
        for _ in range(100):
            if any(
                event.segment_id == "after-failover"
                and event.stage is CaptionStage.COMMITTED
                for event in runtime.store.snapshot()
            ):
                break
            await asyncio.sleep(0.01)
        assert any(
            event.segment_id == "after-failover"
            and event.stage is CaptionStage.COMMITTED
            for event in runtime.store.snapshot()
        )
    finally:
        await runtime.stop()
    stored = [
        event for event in runtime.history.segments(state.session_id)
        if event.segment_id == "after-failover"
    ]
    assert len(stored) == 1
    assert stored[0].stage is CaptionStage.COMMITTED
    assert stored[0].translations["en"]
