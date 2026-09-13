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
