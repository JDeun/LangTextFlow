from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

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

    @abstractmethod
    async def start(self, request: StartSessionRequest) -> None: ...

    @abstractmethod
    async def feed_audio(self, pcm_f32le: bytes) -> None: ...

    @abstractmethod
    async def end_audio(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
