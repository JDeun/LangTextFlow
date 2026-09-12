from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets

from langtextflow.asr.base import AsrEngine, AsrEngineError, PublishEvent
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent

logger = logging.getLogger(__name__)


def vibevoice_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("VibeVoice URL must be an http(s) URL")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/v1/stream"


class VibeVoiceStreamingAsrEngine(AsrEngine):
    """Adapter for Microsoft's VibeVoice vLLM streaming WebSocket server."""

    def __init__(
        self,
        publish: PublishEvent,
        *,
        base_url: str,
        queue_chunks: int = 32,
        max_frame_bytes: int = 1024 * 1024,
    ) -> None:
        super().__init__(publish)
        self.base_url = base_url.rstrip("/")
        self.queue_chunks = queue_chunks
        self.max_frame_bytes = max_frame_bytes
        self._sample_rate = 16000
        self._chunk_seconds = 2.0
        self._request: StartSessionRequest | None = None
        self._ws: Any | None = None
        self._queue: asyncio.Queue[bytes | None] | None = None
        self._sender_task: asyncio.Task[None] | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._ended = False
        self._sequence = 0
        self._failure: str | None = None

    @property
    def running(self) -> bool:
        return self._ws is not None and self._failure is None

    @property
    def accepts_audio(self) -> bool:
        return True

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize() if self._queue is not None else 0

    @property
    def queue_capacity(self) -> int:
        return self.queue_chunks

    @property
    def failure(self) -> str | None:
        return self._failure

    async def start(self, request: StartSessionRequest) -> None:
        if self.running:
            return
        self._failure = None
        self._request = request
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/v1/config")
                response.raise_for_status()
                config = response.json()
            self._sample_rate = int(config["sample_rate"])
            self._chunk_seconds = float(config.get("chunk_seconds", 2.0))
            self._ws = await websockets.connect(vibevoice_ws_url(self.base_url), max_size=None)
            await self._ws.send(
                json.dumps(
                    {
                        "context_info": self._context_info(request),
                        "max_tokens": 256,
                        "temperature": 0.0,
                        "top_p": 1.0,
                    }
                )
            )
        except Exception as exc:
            self._record_failure("startup", exc)
            await self._close_socket()
            raise AsrEngineError(f"VibeVoice server is unavailable: {exc}") from exc

        self._queue = asyncio.Queue(maxsize=self.queue_chunks)
        self._ended = False
        self._sequence = 0
        self._sender_task = asyncio.create_task(self._send_loop(), name="vibevoice-audio-sender")
        self._receiver_task = asyncio.create_task(
            self._receive_loop(), name="vibevoice-transcript-receiver"
        )

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self._failure is not None:
            raise AsrEngineError(self._failure)
        if not self.running or self._queue is None or self._ended:
            raise AsrEngineError("VibeVoice audio stream is not active")
        if not pcm_f32le or len(pcm_f32le) % 4:
            raise ValueError("audio frame must contain little-endian float32 PCM")
        if len(pcm_f32le) > self.max_frame_bytes:
            raise ValueError("audio frame is larger than the configured safety limit")
        await self._queue.put(pcm_f32le)

    async def end_audio(self) -> None:
        if self._queue is None or self._ended or self._failure is not None:
            return
        self._ended = True
        await self._queue.put(None)

    async def stop(self) -> None:
        await self.end_audio()
        if self._sender_task is not None:
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._sender_task, timeout=2.0)
        if self._receiver_task is not None and not self._receiver_task.done():
            self._receiver_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._receiver_task
        await self._close_socket()
        self._sender_task = None
        self._receiver_task = None
        self._queue = None
        self._request = None

    async def _send_loop(self) -> None:
        assert self._queue is not None
        assert self._ws is not None
        try:
            while True:
                frame = await self._queue.get()
                try:
                    if frame is None:
                        await self._ws.send("end")
                        return
                    await self._ws.send(frame)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_failure("audio sender", exc)
            logger.exception("VibeVoice audio sender stopped unexpectedly")
            await self._close_socket()

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        assert self._request is not None
        completed = False
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if message.get("error"):
                    raise AsrEngineError(str(message["error"]))
                if message.get("done"):
                    completed = True
                    return
                text = str(message.get("text", "")).strip()
                if not text:
                    continue
                self._sequence += 1
                start_ms = int((self._sequence - 1) * self._chunk_seconds * 1000)
                await self.publish(
                    TranscriptEvent(
                        segment_id=f"vibe-{self._sequence:08d}",
                        version=1,
                        stage=CaptionStage.STABLE,
                        source_language=self._request.source_language,
                        text=text,
                        start_ms=start_ms,
                        end_ms=int(start_ms + self._chunk_seconds * 1000),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_failure("transcript receiver", exc)
            logger.exception("VibeVoice streaming receiver stopped unexpectedly")
            await self._close_socket()
        finally:
            if not completed and not self._ended and self._failure is None:
                self._record_failure("transcript receiver", "stream closed unexpectedly")
                await self._close_socket()

    def _record_failure(self, stage: str, error: object) -> None:
        if self._failure is None:
            self._failure = f"VibeVoice {stage} failed: {error}"

    async def _close_socket(self) -> None:
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
        self._ws = None

    @staticmethod
    def _context_info(request: StartSessionRequest) -> str | None:
        context = request.context
        parts = [context.title]
        if context.presenter:
            parts.append(context.presenter)
        if context.description:
            parts.append(context.description[:1000])
        hotwords = context.asr_hotwords()
        if hotwords:
            parts.append(", ".join(hotwords))
        value = " | ".join(part.strip() for part in parts if part.strip())
        return value or None
