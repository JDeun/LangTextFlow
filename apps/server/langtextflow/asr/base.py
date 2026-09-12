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

    @abstractmethod
    async def start(self, request: StartSessionRequest) -> None: ...

    @abstractmethod
    async def feed_audio(self, pcm_f32le: bytes) -> None: ...

    @abstractmethod
    async def end_audio(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
