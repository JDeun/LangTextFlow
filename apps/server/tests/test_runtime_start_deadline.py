import asyncio
import sqlite3
import threading

import pytest

from langtextflow.asr.base import AsrEngineError
from langtextflow.config import Settings
from langtextflow.models import CaptionStage, SessionContext, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["correction", "translation"])
async def test_optional_prepare_timeout_keeps_raw_captions_and_session_usable(
    tmp_path, monkeypatch, provider,
):
    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "prepare.db"), provider_prepare_timeout_seconds=.02,
    ))

    class StalledProvider:
        model = "stalled"

        async def prepare(self):
            await asyncio.Event().wait()

        async def correct(self, *args, **kwargs):
            pytest.fail("unavailable corrector must not run")

        async def translate(self, *args, **kwargs):
            pytest.fail("unavailable translator must not run")

        async def close(self):
            pass

    monkeypatch.setattr(
        runtime.pipeline, "_build_corrector" if provider == "correction" else "_build_translator",
        lambda request: StalledProvider(),
    )
    try:
        await asyncio.wait_for(runtime.start(StartSessionRequest(
            engine="mock", translation_provider="none",
        )), 1)
        status = (runtime.pipeline.correction_status if provider == "correction"
                  else runtime.pipeline.status)
        assert not status.available and "timed out" in status.error
        await runtime.pipeline.ingest(TranscriptEvent(
            segment_id="raw-after-prepare", text="original speech", original_text="original speech",
            version=1, source_language="ko", start_ms=0, end_ms=10, stage=CaptionStage.STABLE,
        ))
        await asyncio.wait_for(runtime.pipeline._queue.join(), 1)
        await asyncio.wait_for(runtime.stop(), 1)
        assert not runtime.state.running and not runtime._lifecycle_lock.locked()
        event = next(e for e in runtime.store.snapshot() if e.segment_id == "raw-after-prepare")
        assert event.committed and event.text == event.original_text == "original speech"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_asr_start_timeout_or_cancel_cleans_failed_session_and_allows_retry(
    tmp_path, monkeypatch, cancel,
):
    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "start.db"), session_start_timeout_seconds=.05,
    ))
    entered = asyncio.Event()
    stopped = asyncio.Event()

    class StalledEngine:
        accepts_audio = False
        sample_rate = 16000
        running = False
        queue_depth = 0
        queue_capacity = 0
        failure = None

        async def start(self, request):
            entered.set()
            await asyncio.Event().wait()

        async def stop(self):
            stopped.set()
            # A late callback from failed startup may not publish during cleanup.
            await self.publish(TranscriptEvent(
                segment_id="late-start", text="must not publish", start_ms=0, end_ms=10,
                version=1, source_language="ko", stage=CaptionStage.STABLE,
            ))

    original_builder = runtime._build_engine
    monkeypatch.setattr(runtime, "_build_engine", lambda request: StalledEngine())
    pending = asyncio.create_task(runtime.start(StartSessionRequest(
        engine="mock", translation_provider="none",
    )))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        stopping = asyncio.create_task(runtime.stop())
        if cancel:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else AsrEngineError):
            await asyncio.wait_for(pending, 1)
        await asyncio.wait_for(stopping, 1)
        assert stopped.is_set() and not runtime.state.running and runtime.engine is None
        assert runtime.pipeline.request is None and runtime._persistence_task is None
        assert not runtime._lifecycle_lock.locked() and not runtime.store.snapshot()
        archived = runtime.history.get_session(runtime.state.session_id)
        assert archived and archived.ended_at is not None
        await asyncio.wait_for(runtime.stop(), 1)
        monkeypatch.setattr(runtime, "_build_engine", original_builder)
        runtime.settings.session_start_timeout_seconds = 1
        assert (await asyncio.wait_for(runtime.start(StartSessionRequest(
            engine="mock", translation_provider="none",
        )), 1)).running
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_global_start_budget_or_cancel_cleans_stalled_prepare(tmp_path, monkeypatch, cancel):
    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "prepare-cancel.db"), session_start_timeout_seconds=.03,
        provider_prepare_timeout_seconds=1,
    ))
    entered, closed = asyncio.Event(), asyncio.Event()

    class StalledTranslator:
        model = "stalled"

        async def prepare(self):
            entered.set()
            await asyncio.Event().wait()

        async def close(self):
            closed.set()

    monkeypatch.setattr(runtime.pipeline, "_build_translator", lambda request: StalledTranslator())
    pending = asyncio.create_task(runtime.start(StartSessionRequest(
        engine="mock", translation_provider="none",
    )))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else AsrEngineError):
            await asyncio.wait_for(pending, 1)
        assert closed.is_set() and runtime.pipeline.request is None and runtime.engine is None
        assert runtime._active_generation is None and not runtime._lifecycle_lock.locked()
        await asyncio.wait_for(runtime.stop(), 1)
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancel_during_session_write_cleans_workers_and_ends_only_late_old_record(
    tmp_path, monkeypatch,
):
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "delayed-write.db")))
    entered, release, old_ended = threading.Event(), threading.Event(), threading.Event()
    create, mark_ended = runtime.history.create_session, runtime.history.mark_ended
    old_id = None

    def delayed_create(state, request):
        if request.context.title == "delayed old session":
            entered.set()
            assert release.wait(3)
        create(state, request)

    def observe_ended(session_id):
        mark_ended(session_id)
        row = runtime.history.get_session(session_id)
        if session_id == old_id and row and row.ended_at is not None:
            old_ended.set()

    monkeypatch.setattr(runtime.history, "create_session", delayed_create)
    monkeypatch.setattr(runtime.history, "mark_ended", observe_ended)
    pending = asyncio.create_task(runtime.start(StartSessionRequest(
        engine="mock", translation_provider="none",
        context=SessionContext(title="delayed old session"),
    )))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        old_id = runtime.state.session_id
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        assert runtime.engine is None and runtime._active_generation is None
        assert runtime.pipeline.request is None and runtime.pipeline._worker_task is None
        assert runtime._persistence_task is None and not runtime._lifecycle_lock.locked()
        current = await asyncio.wait_for(runtime.start(StartSessionRequest(
            engine="mock", translation_provider="none", context=SessionContext(title="new session"),
        )), 1)
        release.set()
        assert await asyncio.to_thread(old_ended.wait, 1)
        assert current.session_id != old_id and current.running
        assert runtime.history.get_session(current.session_id).ended_at is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancel_during_locked_sqlite_write_does_not_wait_for_write_lock(
    tmp_path, monkeypatch,
):
    path = str(tmp_path / "locked-write.db")
    runtime = CaptionRuntime(Settings(database_path=path))
    await runtime.initialize()
    entered = threading.Event()
    create = runtime.history.create_session

    def observe_create(state, request):
        entered.set()
        create(state, request)

    monkeypatch.setattr(runtime.history, "create_session", observe_create)
    lock = sqlite3.connect(path)
    lock.execute("BEGIN IMMEDIATE")
    pending = asyncio.create_task(runtime.start(StartSessionRequest(
        engine="mock", translation_provider="none",
    )))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        session_id = runtime.state.session_id
        pending.cancel()
        await asyncio.wait({pending}, timeout=1)
        assert pending.done() and not runtime._lifecycle_lock.locked()
        assert runtime.engine is None and runtime.pipeline.request is None
    finally:
        lock.rollback()
        lock.close()
        await asyncio.gather(pending, return_exceptions=True)
    try:
        for _ in range(100):
            row = await asyncio.to_thread(runtime.history.get_session, session_id)
            if row and row.ended_at is not None:
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail("cancelled session write was never finalized")
    finally:
        await runtime.shutdown()
