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
import stat
import sys
import threading
import wave
from array import array
from contextlib import suppress
from uuid import uuid4

from .post_event_archive import PostEventArchive
from .recording_journal import MAX_DURATION_SECONDS, MAX_MEDIA_BYTES
from .storage_guard import ensure_storage_capacity


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
        start_cancelled: threading.Event | None = None,
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
        self._open_cancelled = start_cancelled if start_cancelled is not None else threading.Event()
        self._open_lock = threading.Lock()
        self._owns_partial = False
        self._partial_identity: tuple[int, int] | None = None
        self.start_offset_ms: float | None = None

    @property
    def error(self) -> str | None:
        return self._failure

    @property
    def frames(self) -> int:
        return self._frames

    async def start(self) -> None:
        try:
            await asyncio.to_thread(self._open)
            if self._open_cancelled.is_set():
                raise asyncio.CancelledError
        except asyncio.CancelledError:
            self._closing = True
            self._open_cancelled.set()
            await asyncio.to_thread(self._discard_open)
            raise
        self._task = asyncio.create_task(
            self._consume(), name=f"event-audio-recording-{self.session_id}",
        )

    def _open(self) -> None:
        if self._open_cancelled.is_set():
            return
        self.archive.audio_root.mkdir(parents=True, exist_ok=True)
        if self.archive.audio_root.is_symlink():
            raise OSError("recording root must not be a symlink")
        ensure_storage_capacity(
            self.archive.audio_root, required_bytes=16 * 1024 * 1024,
            reserve_bytes=self.reserve_bytes, operation="start session recording",
        )
        fd = os.open(
            self._partial,
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        info = os.fstat(fd)
        identity = info.st_dev, info.st_ino
        try:
            stream = os.fdopen(fd, "w+b")
        except Exception:
            os.close(fd)
            self._remove_owned_partial(identity)
            raise
        try:
            writer = wave.open(stream, "wb")  # noqa: SIM115 - closed on stop()
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(self.sample_rate)
            with self._open_lock:
                cancelled = self._open_cancelled.is_set()
                if not cancelled:
                    self._wave = writer
                    self._stream = stream
                    self._owns_partial = True
                    self._partial_identity = identity
            if cancelled:
                with suppress(OSError, ValueError, wave.Error):
                    writer.close()
                stream.close()
                self._remove_owned_partial(identity)
        except Exception:
            stream.close()
            self._remove_owned_partial(identity)
            raise

    def _discard_open(self) -> None:
        with self._open_lock:
            writer, stream = self._wave, self._stream
            owned = self._owns_partial
            identity = self._partial_identity
            self._wave = self._stream = None
            self._owns_partial = False
            self._partial_identity = None
        if writer is not None:
            with suppress(OSError, ValueError, wave.Error):
                writer.close()
        if stream is not None:
            with suppress(OSError):
                stream.close()
        if owned:
            self._remove_owned_partial(identity)

    def _remove_owned_partial(self, identity) -> None:
        with suppress(OSError):
            info = self._partial.stat(follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                self._partial.unlink()

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
        size, digest = 0, hashlib.sha256()
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
                    size = os.fstat(self._stream.fileno()).st_size
                    if size > MAX_MEDIA_BYTES or size != self._frames * 2 + 44:
                        raise OSError("recording size changed")
                    # Hash the descriptor we created, not a possibly replaced
                    # pathname, and never read beyond the known PCM size.
                    self._stream.seek(0)
                    remaining = size
                    while remaining:
                        block = self._stream.read(min(1024 * 1024, remaining))
                        if not block:
                            raise OSError("recording was truncated")
                        digest.update(block)
                        remaining -= len(block)
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
            self._remove_owned_partial(self._partial_identity)
            return 0, ""
        return size, digest.hexdigest()

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
            reservation = await asyncio.to_thread(
                self.archive.recording_journal.reserve,
                self.session_id,
                filename=self._filename,
                size_bytes=size,
                sha256=digest,
                duration_seconds=self._frames / self.sample_rate,
                sample_rate=self.sample_rate,
                channels=1,
                start_offset_ms=self.start_offset_ms,
            )
        except Exception:
            await asyncio.to_thread(self._remove_owned_partial, self._partial_identity)
            raise
        # After reservation, preserve a failed promotion for verified recovery.
        return await asyncio.to_thread(self.archive.recording_journal.promote, reservation)
