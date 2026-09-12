from __future__ import annotations

import asyncio
from contextlib import suppress

from .base import AsrEngine, PublishEvent
from ..models import CaptionStage, StartSessionRequest, TranscriptEvent


SAMPLES = [
    (
        "오늘 우리가 볼 말씀은 요한 보금 삼장입니다",
        "오늘 우리가 볼 말씀은 요한복음 3장입니다.",
        "Today we will look at John chapter 3.",
    ),
    (
        "하나님이 세상을 이처럼 사랑하사 독생자를 주셨으니",
        "하나님이 세상을 이처럼 사랑하사 독생자를 주셨으니",
        "For God so loved the world that He gave His one and only Son.",
    ),
    (
        "오늘 말씀을 통해 복음의 의미를 함께 살펴보겠습니다",
        "오늘 말씀을 통해 복음의 의미를 함께 살펴보겠습니다.",
        "Today, we will consider the meaning of the gospel together.",
    ),
]


class MockStreamingAsrEngine(AsrEngine):
    """Deterministic engine used to develop UI/state semantics before GPU ASR lands."""

    def __init__(self, publish: PublishEvent) -> None:
        super().__init__(publish)
        self._task: asyncio.Task[None] | None = None
        self._request: StartSessionRequest | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, request: StartSessionRequest) -> None:
        if self.running:
            return
        self._request = request
        self._task = asyncio.create_task(self._run(), name="mock-streaming-asr")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        assert self._request is not None
        cycle = 0
        while True:
            for index, (raw, corrected, english) in enumerate(SAMPLES):
                segment_id = f"demo-{cycle:03d}-{index:02d}"
                start_ms = (cycle * len(SAMPLES) + index) * 6000
                await self._emit(segment_id, 1, CaptionStage.PARTIAL, raw[:-3], start_ms)
                await asyncio.sleep(0.55)
                await self._emit(segment_id, 2, CaptionStage.STABLE, raw, start_ms)
                await asyncio.sleep(0.65)
                await self._emit(segment_id, 3, CaptionStage.CORRECTED, corrected, start_ms)
                await asyncio.sleep(0.65)
                translations = {
                    lang: english if lang == "en" else f"[{lang}] {corrected}"
                    for lang in self._request.target_languages
                }
                await self._emit(
                    segment_id,
                    4,
                    CaptionStage.TRANSLATED,
                    corrected,
                    start_ms,
                    translations=translations,
                )
                await asyncio.sleep(0.55)
                await self._emit(
                    segment_id,
                    5,
                    CaptionStage.COMMITTED,
                    corrected,
                    start_ms,
                    translations=translations,
                    committed=True,
                )
                await asyncio.sleep(1.1)
            cycle += 1

    async def _emit(
        self,
        segment_id: str,
        version: int,
        stage: CaptionStage,
        text: str,
        start_ms: int,
        *,
        translations: dict[str, str] | None = None,
        committed: bool = False,
    ) -> None:
        assert self._request is not None
        await self.publish(
            TranscriptEvent(
                segment_id=segment_id,
                version=version,
                stage=stage,
                source_language=self._request.source_language,
                text=text,
                translations=translations or {},
                start_ms=start_ms,
                end_ms=start_ms + 5000 if committed else None,
                confidence=0.93 if stage is not CaptionStage.PARTIAL else 0.72,
                committed=committed,
            )
        )
