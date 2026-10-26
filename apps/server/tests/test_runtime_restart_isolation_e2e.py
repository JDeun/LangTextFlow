"""Restart isolation across ASR callbacks, live captions, and durable history."""
from __future__ import annotations

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_restart_rejects_old_asr_callback_and_keeps_sessions_separate(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "restart.sqlite3")))
    request = StartSessionRequest(
        engine="mock", source_language="ko", target_languages=["en"],
        translation_provider="demo",
    )
    first = await runtime.start(request)
    assert first.session_id is not None
    assert runtime.engine is not None
    old_callback = runtime.engine.publish
    second = await runtime.start(request)
    assert second.session_id is not None
    assert first.session_id != second.session_id
    assert runtime.engine is not None
    try:
        await old_callback(
            TranscriptEvent(
                segment_id="stale-after-restart", version=1,
                stage=CaptionStage.STABLE, source_language="ko",
                text="이전 세션", start_ms=0, end_ms=1000,
            )
        )
        await runtime.engine.publish(
            TranscriptEvent(
                segment_id="fresh-after-restart", version=1,
                stage=CaptionStage.STABLE, source_language="ko",
                text="새 세션", start_ms=0, end_ms=1000,
            )
        )
        for _ in range(100):
            if any(
                event.segment_id == "fresh-after-restart"
                and event.stage is CaptionStage.COMMITTED
                for event in runtime.store.snapshot()
            ):
                break
            await asyncio.sleep(0.01)
        assert runtime.stale_asr_events_dropped >= 1
        assert not any(
            event.segment_id == "stale-after-restart"
            for event in runtime.store.snapshot()
        )
        assert any(
            event.segment_id == "fresh-after-restart"
            and event.stage is CaptionStage.COMMITTED
            for event in runtime.store.snapshot()
        )
    finally:
        await runtime.stop()
    assert not any(
        event.segment_id == "stale-after-restart"
        for event in runtime.history.segments(second.session_id)
    )
    assert any(
        event.segment_id == "fresh-after-restart"
        and event.stage is CaptionStage.COMMITTED
        for event in runtime.history.segments(second.session_id)
    )
    assert not any(
        event.segment_id == "fresh-after-restart"
        for event in runtime.history.segments(first.session_id)
    )
    assert runtime.history.get_session(first.session_id).ended_at is not None
    assert runtime.history.get_session(second.session_id).ended_at is not None
