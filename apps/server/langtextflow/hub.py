from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass

from fastapi import WebSocket

from .models import TranscriptEvent


@dataclass
class _ClientDelivery:
    queue: asyncio.Queue[tuple[dict[str, object], asyncio.Future[bool] | None]]
    writer: asyncio.Task[None] | None = None


class WebSocketHub:
    """Order-preserving caption fan-out with bounded per-client backpressure.

    Async broadcast() remains an acknowledgement barrier for tests and explicit
    callers; enqueue() is the nonblocking hot-path used by CaptionRuntime.
    """

    def __init__(
        self, *, max_clients: int = 256, send_timeout_seconds: float = 0.75,
        max_pending_messages: int = 32,
    ) -> None:
        self.max_clients = max(1, max_clients)
        self.send_timeout_seconds = max(0.05, send_timeout_seconds)
        self.max_pending_messages = max(1, max_pending_messages)
        self._clients: dict[WebSocket, _ClientDelivery] = {}
        self._connect_lock = asyncio.Lock()
        self._closing_tasks: set[asyncio.Task[None]] = set()
        # Admission/eviction statistics are process-local telemetry only; no
        # client addresses, transcript contents or credentials are retained.
        self.clients_rejected = 0
        self.clients_evicted_overflow = 0
        self.clients_evicted_send_failure = 0
        self.client_queue_high_watermark = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(
        self, websocket: WebSocket, *,
        initial_snapshot: Callable[[], dict[str, object]] | None = None,
    ) -> bool:
        async with self._connect_lock:
            if len(self._clients) >= self.max_clients:
                self.clients_rejected += 1
                await websocket.close(code=1013, reason="caption client capacity reached")
                return False
            await websocket.accept()
            pending = _ClientDelivery(
                queue=asyncio.Queue(maxsize=self.max_pending_messages)
            )
            if initial_snapshot is not None:
                # Capture after accept, enqueue before registration. The
                # registration/snapshot boundary contains no await; events
                # produced during accept are included in the snapshot.
                pending.queue.put_nowait((initial_snapshot(), None))
            self._clients[websocket] = pending
            pending.writer = asyncio.create_task(
                self._send_loop(websocket, pending), name="caption-socket-writer"
            )
            return True

    def disconnect(self, websocket: WebSocket) -> None:
        client = self._clients.pop(websocket, None)
        if client is None:
            return
        if client.writer is not None and client.writer is not asyncio.current_task():
            client.writer.cancel()
        while True:
            try:
                _, acknowledgment = client.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if acknowledgment is not None and not acknowledgment.done():
                acknowledgment.set_result(False)
            client.queue.task_done()

    async def _send_one(self, client: WebSocket, payload: dict[str, object]) -> bool:
        try:
            await asyncio.wait_for(
                client.send_json(payload),
                timeout=self.send_timeout_seconds,
            )
            return True
        except Exception:
            return False

    async def _send_loop(self, websocket: WebSocket, client: _ClientDelivery) -> None:
        try:
            while True:
                payload, acknowledgment = await client.queue.get()
                try:
                    succeeded = await self._send_one(websocket, payload)
                    if acknowledgment is not None and not acknowledgment.done():
                        acknowledgment.set_result(succeeded)
                    if not succeeded:
                        self.clients_evicted_send_failure += 1
                        self.disconnect(websocket)
                        await self._close_one(
                            websocket, code=1013, reason="caption delivery timed out"
                        )
                        return
                finally:
                    if acknowledgment is not None and not acknowledgment.done():
                        acknowledgment.set_result(False)
                    client.queue.task_done()
        except asyncio.CancelledError:
            raise

    def _schedule_close(self, client: WebSocket, *, reason: str) -> None:
        task = asyncio.create_task(
            self._close_one(client, code=1013, reason=reason),
            name="caption-socket-close",
        )
        self._closing_tasks.add(task)
        task.add_done_callback(self._closing_tasks.discard)

    def _offer(
        self, websocket: WebSocket, payload: dict[str, object],
        acknowledgment: asyncio.Future[bool] | None = None,
    ) -> bool:
        client = self._clients.get(websocket)
        if client is None:
            if acknowledgment is not None and not acknowledgment.done():
                acknowledgment.set_result(False)
            return False
        try:
            client.queue.put_nowait((payload, acknowledgment))
            self.client_queue_high_watermark = max(
                self.client_queue_high_watermark, client.queue.qsize(),
            )
            return True
        except asyncio.QueueFull:
            self.clients_evicted_overflow += 1
            # Resync via a new authorized snapshot instead of silently losing
            # middle revisions while keeping a client connected.
            self.disconnect(websocket)
            self._schedule_close(websocket, reason="caption client queue overflow")
            if acknowledgment is not None and not acknowledgment.done():
                acknowledgment.set_result(False)
            return False

    def enqueue(self, event: TranscriptEvent) -> None:
        """Publish without waiting on any audience socket, database or network."""
        if not self._clients:
            return
        payload = event.model_dump(mode="json")
        for client in tuple(self._clients):
            self._offer(client, payload)

    async def broadcast(self, event: TranscriptEvent) -> None:
        """Explicit acknowledgement barrier; does not affect ASR hot path."""
        if not self._clients:
            return
        payload = event.model_dump(mode="json")
        acknowledgments: list[asyncio.Future[bool]] = []
        loop = asyncio.get_running_loop()
        for client in tuple(self._clients):
            acknowledgment: asyncio.Future[bool] = loop.create_future()
            acknowledgments.append(acknowledgment)
            self._offer(client, payload, acknowledgment)
        if acknowledgments:
            await asyncio.gather(*acknowledgments)

    async def _close_one(self, client: WebSocket, *, code: int, reason: str) -> None:
        with suppress(Exception):
            await asyncio.wait_for(
                client.close(code=code, reason=reason),
                timeout=self.send_timeout_seconds,
            )

    async def close_all(
        self, *, code: int = 1012,
        reason: str = "caption session changed",
    ) -> None:
        async with self._connect_lock:
            clients = tuple(self._clients)
            writers = [
                state.writer for state in self._clients.values()
                if state.writer is not None
            ]
            for client in clients:
                self.disconnect(client)
        if writers:
            await asyncio.gather(*writers, return_exceptions=True)
        if clients:
            await asyncio.gather(
                *(self._close_one(client, code=code, reason=reason) for client in clients)
            )
        if self._closing_tasks:
            await asyncio.gather(*tuple(self._closing_tasks), return_exceptions=True)
