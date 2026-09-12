from abc import ABC, abstractmethod

from ..models import SessionContext


class TranslationError(RuntimeError):
    """A translation provider is unavailable or returned an invalid result."""


class Translator(ABC):
    provider: str
    model: str | None = None

    async def prepare(self) -> None:
        return

    @abstractmethod
    async def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> str: ...

    async def close(self) -> None:
        return
