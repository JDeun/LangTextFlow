import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import suppress

from ..models import StartSessionRequest, TranscriptEvent

PublishEvent = Callable[[TranscriptEvent], Awaitable[None]]


class AsrEngineError(RuntimeError):
    """Raised when an ASR provider cannot start or continue safely."""


class AsrEngine(ABC):
    """Contract implemented by every streaming ASR backend."""

    def __init__(self, publish: PublishEvent) -> None:
        self.publish = publish

    @property
    @abstractmethod
    def running(self) -> bool: ...

    @property
    @abstractmethod
    def accepts_audio(self) -> bool: ...

    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    @property
    def queue_depth(self) -> int:
        return 0

    @property
    def queue_capacity(self) -> int:
        return 0

    @property
    def failure(self) -> str | None:
        """Return the current fatal provider failure, if any."""
        return None

    @property
    def failover_count(self) -> int:
        """Number of successful mid-session provider handoffs."""
        return 0

    @property
    def last_failover_reason(self) -> str | None:
        """Most recent reason that caused a successful provider handoff."""
        return None

    async def _enqueue_audio(
        self, queue: asyncio.Queue[bytes | None], frame: bytes,
        *consumers: asyncio.Task[None],
    ) -> None:
        try:
            queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass
        pending = asyncio.create_task(queue.put(frame))
        try:
            # Backpressure must end when its consumer fails or is stopped.
            await asyncio.wait([pending, *consumers], return_when=asyncio.FIRST_COMPLETED)
            if not pending.done() or any(task.done() for task in consumers):
                raise AsrEngineError(self.failure or "ASR audio consumer stopped")
            await pending
        finally:
            if not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending

    @abstractmethod
    async def start(self, request: StartSessionRequest) -> None: ...

    @abstractmethod
    async def feed_audio(self, pcm_f32le: bytes) -> None: ...

    @abstractmethod
    async def end_audio(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
