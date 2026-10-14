from __future__ import annotations

import asyncio

import pytest

from langtextflow.hub import WebSocketHub
from langtextflow.models import CaptionStage, TranscriptEvent


class LoadSocket:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.messages = 0
        self.closed = False

    async def accept(self) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.closed = True

    async def send_json(self, payload: dict[str, object]) -> None:
        del payload
        if self.delay:
            await asyncio.sleep(self.delay)
        self.messages += 1


def _event() -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="fanout-load",
        version=1,
        stage=CaptionStage.STABLE,
        source_language="ko",
        text="동시 송출 부하 회귀 테스트",
        start_ms=0,
        end_ms=1000,
    )


@pytest.mark.asyncio
async def test_hub_fanout_delivers_to_many_clients() -> None:
    hub = WebSocketHub(max_clients=128, send_timeout_seconds=0.1)
    clients = [LoadSocket() for _ in range(128)]
    connected = await asyncio.gather(*(hub.connect(client) for client in clients))
    assert all(connected)
    assert hub.client_count == 128

    await hub.broadcast(_event())

    assert all(client.messages == 1 for client in clients)
    assert hub.client_count == 128
    await hub.close_all()


@pytest.mark.asyncio
async def test_hub_fanout_evicts_slow_clients_without_dropping_fast_clients() -> None:
    hub = WebSocketHub(max_clients=64, send_timeout_seconds=0.01)
    fast = [LoadSocket() for _ in range(56)]
    slow = [LoadSocket(delay=0.1) for _ in range(8)]
    clients = [*fast, *slow]
    connected = await asyncio.gather(*(hub.connect(client) for client in clients))
    assert all(connected)

    await hub.broadcast(_event())

    assert all(client.messages == 1 for client in fast)
    assert all(client.messages == 0 for client in slow)
    assert hub.client_count == len(fast)
    assert hub.clients_evicted_send_failure == len(slow)
    assert hub.clients_evicted_overflow == 0
    await hub.close_all()


class _RecordedSocket(LoadSocket):
    def __init__(self, *, delay: float = 0.0) -> None:
        super().__init__(delay=delay)
        self.payloads: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        await super().send_json(payload)
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_snapshot_is_first_even_if_followup_event_is_immediate() -> None:
    hub = WebSocketHub()
    client = _RecordedSocket()
    await hub.connect(
        client,
        initial_snapshot=lambda: {"type": "snapshot", "segments": []},
    )
    hub.enqueue(_event())
    for _ in range(200):
        if len(client.payloads) == 2:
            break
        await asyncio.sleep(0.001)
    assert len(client.payloads) == 2
    assert client.payloads[0] == {"type": "snapshot", "segments": []}
    assert client.payloads[1]["segment_id"] == "fanout-load"
    await hub.close_all()


@pytest.mark.asyncio
async def test_nonblocking_enqueue_avoids_stalled_client() -> None:
    hub = WebSocketHub(send_timeout_seconds=0.05, max_pending_messages=4)
    fast = _RecordedSocket()
    slow = _RecordedSocket(delay=0.5)
    assert await hub.connect(fast)
    assert await hub.connect(slow)
    async def send_many() -> None:
        hub.enqueue(_event())
        hub.enqueue(_event())
    await asyncio.wait_for(send_many(), timeout=0.05)
    for _ in range(150):
        if fast.messages == 2 and hub.client_count == 1:
            break
        await asyncio.sleep(0.001)
    assert fast.messages == 2
    assert slow.messages == 0
    assert hub.client_count == 1
    await hub.close_all()


@pytest.mark.asyncio
async def test_slow_client_overflow_evicts_without_silent_revision_loss() -> None:
    hub = WebSocketHub(send_timeout_seconds=0.3, max_pending_messages=2)
    slow = _RecordedSocket(delay=0.5)
    assert await hub.connect(slow)
    for version in range(1, 6):
        hub.enqueue(_event().model_copy(update={"version": version}))
    assert hub.client_count == 0
    assert hub.clients_evicted_overflow == 1
    assert hub.client_queue_high_watermark == 2
    await hub.close_all()
    assert slow.closed is True


@pytest.mark.asyncio
async def test_runtime_source_publish_does_not_wait_for_audience_send(tmp_path) -> None:
    from langtextflow.config import Settings
    from langtextflow.runtime import CaptionRuntime

    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "fanout.db")))
    stalled = _RecordedSocket(delay=0.5)
    await runtime.hub.connect(stalled)
    try:
        # This fails with the old await hub.broadcast() runtime path,
        # because the slow subscriber takes much longer than the deadline.
        await asyncio.wait_for(runtime._publish(_event()), timeout=0.1)
        assert runtime.store.snapshot()[0].text == _event().text
    finally:
        await runtime.hub.close_all()


@pytest.mark.asyncio
async def test_hub_capacity_rejection_is_distinct_from_delivery_failure() -> None:
    hub = WebSocketHub(max_clients=1, send_timeout_seconds=0.1)
    first = LoadSocket()
    rejected = LoadSocket()
    assert await hub.connect(first)
    assert not await hub.connect(rejected)
    assert rejected.closed is True
    assert hub.clients_rejected == 1
    assert hub.clients_evicted_overflow == 0
    assert hub.clients_evicted_send_failure == 0
    await hub.close_all()
