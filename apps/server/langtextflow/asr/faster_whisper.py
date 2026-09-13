from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from langtextflow.asr.base import AsrEngine, AsrEngineError, PublishEvent
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent


@dataclass(frozen=True)
class DecodedSegment:
    start_seconds: float
    end_seconds: float
    text: str


class FasterWhisperStreamingAsrEngine(AsrEngine):
    """Micro-batched streaming adapter around faster-whisper."""

    def __init__(
        self,
        publish: PublishEvent,
        *,
        model: str = "small",
        device: str = "auto",
        compute_type: str = "default",
        chunk_seconds: float = 4.0,
        queue_chunks: int = 32,
        max_frame_bytes: int = 1024 * 1024,
    ) -> None:
        super().__init__(publish)
        if chunk_seconds <= 0:
            raise ValueError("faster-whisper chunk_seconds must be greater than zero")
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.chunk_seconds = chunk_seconds
        self.queue_chunks = queue_chunks
        self.max_frame_bytes = max_frame_bytes
        self._sample_rate = 16000
        self._request: StartSessionRequest | None = None
        self._model: Any | None = None
        self._queue: asyncio.Queue[bytes | None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._ended = False
        self._sequence = 0
        self._audio_cursor_ms = 0.0
        self._failure: str | None = None

    @property
    def running(self) -> bool:
        return (
            self._worker_task is not None
            and not self._worker_task.done()
            and self._failure is None
        )

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
            self._model = await asyncio.to_thread(self._load_model_sync)
        except AsrEngineError as exc:
            self._failure = str(exc)
            raise
        except Exception as exc:
            self._failure = f"faster-whisper model failed to load: {exc}"
            raise AsrEngineError(self._failure) from exc

        self._queue = asyncio.Queue(maxsize=self.queue_chunks)
        self._ended = False
        self._sequence = 0
        self._audio_cursor_ms = 0.0
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="faster-whisper-inference",
        )

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        if self._failure is not None:
            raise AsrEngineError(self._failure)
        if not self.running or self._queue is None or self._ended:
            raise AsrEngineError("faster-whisper audio stream is not active")
        if not pcm_f32le or len(pcm_f32le) % 4:
            raise ValueError("audio frame must contain little-endian float32 PCM")
        if len(pcm_f32le) > self.max_frame_bytes:
            raise ValueError("audio frame is larger than the configured safety limit")
        await self._queue.put(pcm_f32le)

    async def end_audio(self) -> None:
        if self._queue is None or self._ended or self._failure is not None:
            return
        self._ended = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            # The worker observes _ended and exits once the existing backlog drains.
            pass

    async def stop(self) -> None:
        await self.end_audio()
        task = self._worker_task
        if task is not None:
            if self._failure is not None and not task.done():
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
        self._worker_task = None
        self._queue = None
        self._request = None
        self._model = None

    def _load_model_sync(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AsrEngineError(
                "faster-whisper is not installed; install LangTextFlow with the 'whisper' extra"
            ) from exc
        return WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )

    def _transcribe_chunk_sync(
        self,
        pcm_f32le: bytes,
        request: StartSessionRequest,
    ) -> list[DecodedSegment]:
        if self._model is None:
            raise AsrEngineError("faster-whisper model is not loaded")
        try:
            import numpy as np
        except ImportError as exc:
            raise AsrEngineError("numpy is required by the faster-whisper runtime") from exc

        audio = np.frombuffer(pcm_f32le, dtype="<f4").astype(np.float32, copy=False)
        context = request.context
        prompt_parts = [context.title]
        if context.presenter:
            prompt_parts.append(context.presenter)
        if context.description:
            prompt_parts.append(context.description[:1000])
        reference = context.reference_excerpt(1200)
        if reference:
            prompt_parts.append(reference)
        initial_prompt = " | ".join(part.strip() for part in prompt_parts if part.strip()) or None
        hotwords = ", ".join(context.asr_hotwords()) or None

        segments, _ = self._model.transcribe(
            audio,
            language=request.source_language,
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=True,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
        )
        decoded: list[DecodedSegment] = []
        for segment in segments:
            text = str(segment.text).strip()
            if not text:
                continue
            decoded.append(
                DecodedSegment(
                    start_seconds=max(0.0, float(segment.start)),
                    end_seconds=max(0.0, float(segment.end)),
                    text=text,
                )
            )
        return decoded

    async def _worker(self) -> None:
        assert self._queue is not None
        assert self._request is not None
        buffer = bytearray()
        bytes_per_second = self._sample_rate * 4
        chunk_bytes = max(4, int(self.chunk_seconds * bytes_per_second))
        chunk_bytes -= chunk_bytes % 4

        try:
            while True:
                frame = await self._queue.get()
                try:
                    if frame is None:
                        if buffer:
                            await self._process_chunk(bytes(buffer))
                        return
                    buffer.extend(frame)
                    while len(buffer) >= chunk_bytes:
                        chunk = bytes(buffer[:chunk_bytes])
                        del buffer[:chunk_bytes]
                        await self._process_chunk(chunk)
                finally:
                    self._queue.task_done()
                if self._ended and self._queue.empty():
                    if buffer:
                        await self._process_chunk(bytes(buffer))
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failure = f"faster-whisper inference worker failed: {exc}"

    async def _process_chunk(self, pcm_f32le: bytes) -> None:
        assert self._request is not None
        base_ms = self._audio_cursor_ms
        duration_ms = len(pcm_f32le) / 4 / self._sample_rate * 1000.0
        decoded = await asyncio.to_thread(
            self._transcribe_chunk_sync,
            pcm_f32le,
            self._request,
        )
        for segment in decoded:
            self._sequence += 1
            start_ms = int(round(base_ms + segment.start_seconds * 1000.0))
            end_ms = int(round(base_ms + segment.end_seconds * 1000.0))
            if end_ms < start_ms:
                end_ms = start_ms
            await self.publish(
                TranscriptEvent(
                    segment_id=f"fw-{self._sequence:08d}",
                    version=1,
                    stage=CaptionStage.STABLE,
                    source_language=self._request.source_language,
                    text=segment.text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                )
            )
        self._audio_cursor_ms += duration_ms
