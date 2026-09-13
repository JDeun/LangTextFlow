import asyncio
import sqlite3
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


@pytest.mark.asyncio
async def test_invalid_engine_does_not_leave_pipeline_worker_running(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    request = StartSessionRequest(
        engine="definitely-invalid",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
    )

    with pytest.raises(ValueError, match="unsupported engine"):
        await runtime.start(request)

    assert runtime.state.running is False
    assert runtime.engine is None
    assert runtime.pipeline._worker_task is None
    assert runtime._persistence_task is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stopped_session_join_code_is_immediately_invalid(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    state = await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    join_code = state.join_code
    assert join_code is not None
    assert runtime.audience_view(join_code).running is True

    await runtime.stop()
    with pytest.raises(KeyError):
        runtime.audience_view(join_code)
    await runtime.shutdown()


class _FakeCaptionSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason or "")

    async def send_json(self, payload: object) -> None:
        del payload


@pytest.mark.asyncio
async def test_new_session_disconnects_existing_caption_subscribers(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    request = StartSessionRequest(
        engine="mock",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
    )
    await runtime.start(request)
    socket = _FakeCaptionSocket()
    assert await runtime.hub.connect(socket) is True
    assert runtime.hub.client_count == 1

    await runtime.start(request)
    assert socket.closed == (1012, "caption session stopped")
    assert runtime.hub.client_count == 0
    await runtime.shutdown()


def test_runtime_threads_configured_audio_frame_limit_into_vad(tmp_path) -> None:
    settings = Settings(
        database_path=str(tmp_path / "runtime.db"),
        max_audio_frame_bytes=2 * 1024 * 1024,
    )
    runtime = CaptionRuntime(settings)
    assert runtime._vad.max_frame_bytes == settings.max_audio_frame_bytes

