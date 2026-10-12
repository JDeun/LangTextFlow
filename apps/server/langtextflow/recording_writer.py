"""Opt-in WAV recording sink; never backpressures the live caption pipeline.

PCM float32 bytes are offered from the existing /ws/audio flow. A bounded worker
turns them into PCM16 WAV on a private file, then atomically promotes a complete
recording and links it to its immutable session ID in SQLite.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import sys
import wave
from array import array
from pathlib import Path
from uuid import uuid4

from .post_event_archive import PostEventArchive
from .storage_guard import ensure_storage_capacity

MAX_DURATION_SECONDS = 4 * 60 * 60
MAX_MEDIA_BYTES = 512 * 1024 * 1024


def pcm_float32_to_pcm16(data: bytes) -> bytes:
    """Convert native float array with explicit little-endian input/output."""
    if not data or len(data) % 4:
        raise ValueError("audio frame must be nonempty float32 PCM")
    floats = array("f")
    floats.frombytes(data)
    if sys.byteorder != "little":
        floats.byteswap()
    if not all(math.isfinite(value) for value in floats):
        raise ValueError("audio frame contains a non-finite sample")
    samples = array("h", (round(max(-1.0, min(1.0, sample)) * 32767) for sample in floats))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


class SessionRecordingWriter:
    """Single-session, bounded async queue. Overflow fails recording, not ASR."""

    def __init__(
        self,
        archive: PostEventArchive,
        *,
        session_id: str,
        sample_rate: int,
        reserve_bytes: int,
        queue_chunks: int = 64,
    ) -> None:
        if not 8_000 <= sample_rate <= 48_000:
            raise ValueError("unsupported recording sample rate")
        if not 1 <= queue_chunks <= 1024:
            raise ValueError("recording queue must have 1–1024 chunks")
        self.archive = archive
        self.session_id = session_id
        self.sample_rate = sample_rate
        self.reserve_bytes = reserve_bytes
        self._filename = f"{uuid4().hex}.wav"
        self._partial = self.archive.audio_root / (self._filename + ".partial")
        self._completed = self.archive.audio_root / self._filename
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_chunks)
        self._task: asyncio.Task[None] | None = None
        self._wave: wave.Wave_write | None = None
        self._stream = None
        self._frames = 0
        self._failure: str | None = None
        self._closing = False

    @property
    def error(self) -> str | None:
        return self._failure

    @property
    def frames(self) -> int:
        return self._frames

    async def start(self) -> None:
        await asyncio.to_thread(self._open)
        self._task = asyncio.create_task(
            self._consume(), name=f"event-audio-recording-{self.session_id}",
        )

    def _open(self) -> None:
        self.archive.audio_root.mkdir(parents=True, exist_ok=True)
        ensure_storage_capacity(
            self.archive.audio_root, required_bytes=16 * 1024 * 1024,
            reserve_bytes=self.reserve_bytes, operation="start session recording",
        )
        fd = os.open(
            self._partial,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        stream = os.fdopen(fd, "wb")
        try:
            writer = wave.open(stream, "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(self.sample_rate)
            self._wave = writer
            self._stream = stream
        except Exception:
            stream.close()
            self._partial.unlink(missing_ok=True)
            raise

    def offer(self, pcm: bytes) -> None:
        """Never await filesystem writes or a full recorder queue in the ASR path."""
        if self._closing or self._failure is not None or self._task is None:
            return
        if not pcm or len(pcm) % 4:
            self._failure = "invalid input frame"
            return
        try:
            self._queue.put_nowait(pcm)
        except asyncio.QueueFull:
            self._failure = "recording queue overflow; incomplete audio discarded"

    def _write(self, pcm: bytes) -> None:
        if self._wave is None:
            raise RuntimeError("recording file is not open")
        frame_count = len(pcm) // 4
        if self._frames + frame_count > MAX_DURATION_SECONDS * self.sample_rate:
            raise ValueError("recording exceeds four-hour maximum")
        if (self._frames + frame_count) * 2 + 44 > MAX_MEDIA_BYTES:
            raise ValueError("recording exceeds 512 MiB limit")
        self._wave.writeframesraw(pcm_float32_to_pcm16(pcm))
        self._frames += frame_count

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                if self._failure is None:
                    try:
                        await asyncio.to_thread(self._write, item)
                    except Exception:
                        self._failure = "recording write failed or exceeded size limits"
            finally:
                self._queue.task_done()

    def _close_file(self) -> tuple[int, str]:
        try:
            if self._wave is not None:
                self._wave.close()
        except (OSError, ValueError, wave.Error):
            self._failure = "recording header finalization failed"
        finally:
            self._wave = None
            if self._stream is not None:
                try:
                    # Complete WAV header and bytes before exposing metadata.
                    self._stream.flush()
                    os.fsync(self._stream.fileno())
                except OSError:
                    self._failure = "recording storage flush failed"
                finally:
                    try:
                        self._stream.close()
                    except OSError:
                        self._failure = "recording storage close failed"
                    self._stream = None
        if self._frames == 0 and self._failure is None:
            self._failure = "recording contains no audio frames"
        if self._failure is not None:
            self._partial.unlink(missing_ok=True)
            return 0, ""
        self._partial.replace(self._completed)
        digest = hashlib.sha256()
        with self._completed.open("rb") as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(block)
        return self._completed.stat().st_size, digest.hexdigest()

    async def stop(self) -> dict | None:
        """Finalize only complete audio; incomplete recording is never advertised."""
        self._closing = True
        if self._task is None:
            return None
        await self._queue.join()
        await self._queue.put(None)
        await self._task
        self._task = None
        size, digest = await asyncio.to_thread(self._close_file)
        if self._failure is not None or size == 0:
            return None
        try:
            return await asyncio.to_thread(
                self.archive.insert_recording,
                self.session_id,
                filename=self._filename,
                size_bytes=size,
                sha256=digest,
                duration_seconds=self._frames / self.sample_rate,
                sample_rate=self.sample_rate,
                channels=1,
            )
        except Exception:
            # Metadata failed: do not leave untracked audio on disk.
            await asyncio.to_thread(self._completed.unlink, missing_ok=True)
            raise
