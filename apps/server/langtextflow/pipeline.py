from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .config import Settings
from .correction import DeterministicCorrector
from .models import (
    CaptionStage,
    StartSessionRequest,
    TranscriptEvent,
    TranslationStatus,
)
from .translation import DemoTranslator, OllamaTranslator, TranslationError, Translator


class CaptionPipeline:
    """Sequential post-ASR pipeline that never blocks source caption delivery."""

    def __init__(self, publish, settings: Settings) -> None:
        self.publish = publish
        self.settings = settings
        self.corrector = DeterministicCorrector()
        self.request: StartSessionRequest | None = None
        self.translator: Translator | None = None
        self.status = TranslationStatus()
        self._queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(maxsize=128)
        self._worker_task: asyncio.Task[None] | None = None

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    async def start(self, request: StartSessionRequest) -> None:
        await self.stop()
        self.request = request
        self._queue = asyncio.Queue(maxsize=128)
        self.translator = self._build_translator(request)
        self.status = TranslationStatus(
            enabled=self.translator is not None,
            provider=request.translation_provider,
            model=getattr(self.translator, "model", None),
            available=self.translator is not None,
        )
        if self.translator is not None:
            try:
                await self.translator.prepare()
            except TranslationError as exc:
                self.status.available = False
                self.status.error = str(exc)
        self._worker_task = asyncio.create_task(self._worker(), name="caption-postprocessing")

    async def ingest(self, event: TranscriptEvent) -> None:
        await self.publish(event)
        if event.stage is CaptionStage.STABLE:
            await self._queue.put(event)

    async def stop(self) -> None:
        if self._worker_task is not None:
            await self._queue.put(None)
            await self._worker_task
            self._worker_task = None
        if self.translator is not None:
            await self.translator.close()
        self.translator = None
        self.request = None

    def _build_translator(self, request: StartSessionRequest) -> Translator | None:
        provider = request.translation_provider
        if provider == "none":
            return None
        if provider == "demo":
            return DemoTranslator()
        if provider == "ollama":
            return OllamaTranslator(
                base_url=self.settings.ollama_url,
                model=request.translation_model or self.settings.ollama_translation_model,
            )
        raise ValueError(f"unsupported translation provider: {provider}")

    async def _worker(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                self._queue.task_done()
                return
            try:
                await self._process_stable(event)
            finally:
                self._queue.task_done()

    async def _process_stable(self, event: TranscriptEvent) -> None:
        assert self.request is not None
        version = event.version + 1
        corrected_text = self.corrector.correct(event.text, self.request.context)
        corrected = self._next_event(
            event,
            version=version,
            stage=CaptionStage.CORRECTED,
            text=corrected_text,
        )
        await self.publish(corrected)

        translations: dict[str, str] = {}
        if self.translator is not None and self.status.available:
            for target in self.request.target_languages:
                try:
                    translations[target] = await self.translator.translate(
                        corrected_text,
                        source_language=self.request.source_language,
                        target_language=target,
                        context=self.request.context,
                    )
                    self.status.error = None
                except TranslationError as exc:
                    self.status.error = str(exc)

        base = corrected
        if translations:
            version += 1
            base = self._next_event(
                corrected,
                version=version,
                stage=CaptionStage.TRANSLATED,
                text=corrected_text,
                translations=translations,
            )
            await self.publish(base)

        version += 1
        committed = self._next_event(
            base,
            version=version,
            stage=CaptionStage.COMMITTED,
            text=corrected_text,
            translations=translations,
            committed=True,
        )
        await self.publish(committed)

    @staticmethod
    def _next_event(
        previous: TranscriptEvent,
        *,
        version: int,
        stage: CaptionStage,
        text: str,
        translations: dict[str, str] | None = None,
        committed: bool = False,
    ) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id=previous.segment_id,
            version=version,
            stage=stage,
            source_language=previous.source_language,
            text=text,
            translations=translations or {},
            start_ms=previous.start_ms,
            end_ms=previous.end_ms,
            speaker=previous.speaker,
            confidence=previous.confidence,
            committed=committed,
            emitted_at=datetime.now(UTC),
        )
