import asyncio
import sqlite3
import struct
from datetime import UTC, datetime

import pytest

from langtextflow.config import Settings
from langtextflow.models import SessionContext, SessionState, StartSessionRequest
from langtextflow.runtime import CaptionRuntime
from langtextflow.session_repository import SessionRepository


@pytest.mark.asyncio
async def test_persistence_worker_exists_only_for_active_session(tmp_path) -> None:
    runtime = CaptionRuntime(
        Settings(database_path=str(tmp_path / "runtime.db"))
    )

    await runtime.initialize()
    assert runtime._persistence_task is None

    await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    assert runtime._persistence_task is not None

    await runtime.stop()
    assert runtime._persistence_task is None
    assert runtime.metrics.persistence_queue_depth == 0

    await runtime.shutdown()
    assert runtime._persistence_task is None


@pytest.mark.asyncio
async def test_runtime_initialization_recovers_previous_open_session(tmp_path) -> None:
    database_path = str(tmp_path / "runtime.db")
    repository = SessionRepository(database_path)
    repository.initialize()
    request = StartSessionRequest(
        engine="mock",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
        context=SessionContext(title="Interrupted service"),
    )
    repository.create_session(
        SessionState(
            session_id="stale-session",
            join_code="ABC234",
            running=True,
            source_language="ko",
            target_languages=["en"],
            engine="mock",
            context=request.context,
            started_at=datetime(2026, 9, 12, 0, 0, tzinfo=UTC),
        ),
        request,
    )

    runtime = CaptionRuntime(Settings(database_path=database_path))
    await runtime.initialize()

    recovered = runtime.history.get_session("stale-session")
    assert recovered is not None
    assert recovered.interrupted is True
    assert recovered.ended_at is not None
    assert runtime._persistence_task is None


@pytest.mark.asyncio
async def test_persistence_disk_full_is_degraded_without_stopping_live_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    await runtime.initialize()

    def fail_upsert(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(runtime.history, "upsert_segment", fail_upsert)
    await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    try:
        for _ in range(100):
            if runtime.state.persistence_error:
                break
            await asyncio.sleep(0.01)

        assert runtime.state.running is True
        assert runtime.state.persistence_error is not None
        assert "disk is full" in runtime.state.persistence_error
        assert runtime.store.snapshot(), "live in-memory captions must survive persistence failure"
    finally:
        await runtime.stop()
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_concurrent_session_starts_are_serialized(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    request = StartSessionRequest(
        engine="mock",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
    )

    original_start = runtime.pipeline.start
    active_starts = 0
    peak_starts = 0

    async def delayed_start(value: StartSessionRequest) -> None:
        nonlocal active_starts, peak_starts
        active_starts += 1
        peak_starts = max(peak_starts, active_starts)
        try:
            await asyncio.sleep(0.02)
            await original_start(value)
        finally:
            active_starts -= 1

    monkeypatch.setattr(runtime.pipeline, "start", delayed_start)

    await asyncio.gather(runtime.start(request), runtime.start(request))
    try:
        assert peak_starts == 1
        assert runtime.state.running is True
        assert runtime._persistence_task is not None
    finally:
        await runtime.stop()
        await runtime.shutdown()


class _BlockingAudioEngine:
    accepts_audio = True
    sample_rate = 16_000
    running = True
    failure = None
    queue_depth = 0
    queue_capacity = 8

    def __init__(self) -> None:
        self.feed_started = asyncio.Event()
        self.allow_feed_to_finish = asyncio.Event()
        self.feed_finished = asyncio.Event()
        self.stop_started = asyncio.Event()
        self.stopped = False

    async def feed_audio(self, _: bytes) -> None:
        self.feed_started.set()
        await self.allow_feed_to_finish.wait()
        self.feed_finished.set()

    async def end_audio(self) -> None:
        return None

    async def stop(self) -> None:
        self.stop_started.set()
        self.stopped = True
        self.running = False


@pytest.mark.asyncio
async def test_audio_stream_claim_is_single_producer_and_session_scoped(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    engine = _BlockingAudioEngine()
    runtime.engine = engine
    runtime.state = SessionState(
        session_id="session-one",
        running=True,
        engine="fake",
        audio_required=True,
        audio_sample_rate=engine.sample_rate,
    )

    assert await runtime.claim_audio_stream() == "session-one"
    assert await runtime.claim_audio_stream() is None

    runtime.state.session_id = "session-two"
    assert await runtime.claim_audio_stream() == "session-two"

    # A stale socket closing must not release the replacement session's lease.
    await runtime.release_audio_stream("session-one")
    assert await runtime.claim_audio_stream() is None

    await runtime.release_audio_stream("session-two")
    assert await runtime.claim_audio_stream() == "session-two"


@pytest.mark.asyncio
async def test_stale_audio_stream_cannot_feed_replacement_session(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    engine = _BlockingAudioEngine()
    engine.allow_feed_to_finish.set()
    runtime.engine = engine
    runtime.state = SessionState(
        session_id="new-session",
        running=True,
        engine="fake",
        audio_required=True,
        audio_sample_rate=engine.sample_rate,
    )
    frame = struct.pack("<f", 0.1) * 32

    with pytest.raises(RuntimeError, match="different session"):
        await runtime.feed_audio(frame, session_id="old-session")
    assert runtime.metrics.audio_frames_received == 0


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_audio_feed(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    engine = _BlockingAudioEngine()
    runtime.engine = engine
    runtime.state = SessionState(
        session_id="audio-race",
        running=True,
        engine="fake",
        audio_required=True,
        audio_sample_rate=engine.sample_rate,
    )
    frame = struct.pack("<f", 0.1) * 32

    feed_task = asyncio.create_task(
        runtime.feed_audio(frame, session_id="audio-race")
    )
    await asyncio.wait_for(engine.feed_started.wait(), timeout=1.0)

    stop_task = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    assert not engine.stop_started.is_set()

    engine.allow_feed_to_finish.set()
    await asyncio.wait_for(feed_task, timeout=1.0)
    await asyncio.wait_for(stop_task, timeout=1.0)

    assert engine.feed_finished.is_set()
    assert engine.stop_started.is_set()
    assert engine.stopped is True
    assert runtime.state.running is False
