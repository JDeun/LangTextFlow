from __future__ import annotations

import asyncio

from fastapi import WebSocket

from .models import TranscriptEvent


class WebSocketHub:
    """Server-push caption fan-out isolated from slow or broken clients."""

    def __init__(self, *, max_clients: int = 256, send_timeout_seconds: float = 0.75) -> None:
        self.max_clients = max(1, max_clients)
        self.send_timeout_seconds = max(0.05, send_timeout_seconds)
        self._clients: set[WebSocket] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> bool:
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
