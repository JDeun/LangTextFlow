"""Runtime fanout integration: slow sockets must not block durable captions."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_slow_websocket_client_does_not_block_caption_commit_or_sqlite(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "slow-socket.sqlite3")))
    state = await runtime.start(
        StartSessionRequest(
            engine="mock", source_language="ko", target_languages=["en"],
            translation_provider="demo",
        )
    )
    socket = AsyncMock()
    socket.send_json.side_effect = lambda _: asyncio.sleep(10)
    runtime.hub.send_timeout_seconds = 0.05
    connected = await runtime.hub.connect(socket)
    assert connected
    try:
        await runtime.pipeline.ingest(
            TranscriptEvent(
                segment_id="slow-client-segment", version=1,
                stage=CaptionStage.STABLE, source_language="ko",
                text="오늘 말씀입니다", start_ms=0, end_ms=1000,
            )
        )
        for _ in range(100):
            if any(
                event.segment_id == "slow-client-segment"
                and event.stage is CaptionStage.COMMITTED
                for event in runtime.store.snapshot()
            ):
                break
            await asyncio.sleep(0.01)
        for _ in range(100):
            if runtime.hub.clients_evicted_send_failure:
                break
            await asyncio.sleep(0.01)
        assert runtime.hub.clients_evicted_send_failure > 0
        assert any(
            event.segment_id == "slow-client-segment"
            and event.stage is CaptionStage.COMMITTED
            for event in runtime.store.snapshot()
        )
    finally:
        await runtime.stop()
    assert state.session_id is not None
    persisted = [
        event for event in runtime.history.segments(state.session_id)
        if event.segment_id == "slow-client-segment"
    ]
    assert len(persisted) == 1
    assert persisted[0].stage is CaptionStage.COMMITTED
    assert persisted[0].translations["en"]
    assert runtime.hub.clients_evicted_send_failure > 0
