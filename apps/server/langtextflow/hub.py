from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocket

from .models import TranscriptEvent


class WebSocketHub:
    """Server-push caption fan-out isolated from slow or broken clients."""

    def __init__(self, *, max_clients: int = 256, send_timeout_seconds: float = 0.75) -> None:
        self.max_clients = max(1, max_clients)
        self.send_timeout_seconds = max(0.05, send_timeout_seconds)
        self._clients: set[WebSocket] = set()
        self._connect_lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> bool:
        # Capacity check and registration must be atomic across the await in accept().
        # Without this lock, concurrent handshakes can each observe free capacity and
        # over-subscribe the hub before either socket is registered.
        async with self._connect_lock:
            if len(self._clients) >= self.max_clients:
                await websocket.close(code=1013, reason="caption client capacity reached")
                return False
            await websocket.accept()
            self._clients.add(websocket)
            return True

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def _send_one(self, client: WebSocket, payload: dict[str, object]) -> bool:
        try:
            await asyncio.wait_for(
                client.send_json(payload),
                timeout=self.send_timeout_seconds,
            )
            return True
        except Exception:
            return False

    async def _close_one(self, client: WebSocket, *, code: int, reason: str) -> None:
        with suppress(Exception):
            await asyncio.wait_for(
                client.close(code=code, reason=reason),
                timeout=self.send_timeout_seconds,
            )

    async def close_all(
        self,
        *,
        code: int = 1012,
        reason: str = "caption session changed",
    ) -> None:
        """Disconnect every subscriber so a new session requires fresh authorization."""
        async with self._connect_lock:
            clients = tuple(self._clients)
            self._clients.clear()
        if not clients:
            return
        await asyncio.gather(
            *(self._close_one(client, code=code, reason=reason) for client in clients),
            return_exceptions=False,
        )

    async def broadcast(self, event: TranscriptEvent) -> None:
        if not self._clients:
            return
        payload = event.model_dump(mode="json")
        clients = tuple(self._clients)
        results = await asyncio.gather(
            *(self._send_one(client, payload) for client in clients),
            return_exceptions=False,
        )
        for client, succeeded in zip(clients, results, strict=True):
            if not succeeded:
                self.disconnect(client)
