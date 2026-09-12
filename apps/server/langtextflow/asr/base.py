from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ..models import StartSessionRequest, TranscriptEvent

PublishEvent = Callable[[TranscriptEvent], Awaitable[None]]


class AsrEngine(ABC):
    """Contract implemented by every streaming ASR backend."""

    def __init__(self, publish: PublishEvent) -> None:
        self.publish = publish

    @abstractmethod
    async def start(self, request: StartSessionRequest) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @property
    @abstractmethod
    def running(self) -> bool: ...
