from __future__ import annotations

import asyncio
from contextlib import suppress

from langtextflow.asr.base import AsrEngine, PublishEvent
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent

SAMPLES = [
    "오늘 우리가 볼 말씀은 요한 보금 삼장입니다",
    "하나님이 세상을 이처럼 사랑하사 독생자를 주셨으니",
    "오늘 말씀을 통해 복음의 의미를 함께 살펴보겠습니다",
]


class MockStreamingAsrEngine(AsrEngine):
    """Deterministic ASR source; post-processing is handled by CaptionPipeline."""

    def __init__(self, publish: PublishEvent) -> None:
        super().__init__(publish)
        self._task: asyncio.Task[None] | None = None
        self._request: StartSessionRequest | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def accepts_audio(self) -> bool:
        return False

    @property
    def sample_rate(self) -> int:
        return 16000

    async def start(self, request: StartSessionRequest) -> None:
        if self.running:
            return
        self._request = request
        self._task = asyncio.create_task(self._run(), name="mock-streaming-asr")

    async def feed_audio(self, pcm_f32le: bytes) -> None:
        del pcm_f32le

    async def end_audio(self) -> None:
        return

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
            for index, raw in enumerate(SAMPLES):
                segment_id = f"demo-{cycle:03d}-{index:02d}"
                start_ms = (cycle * len(SAMPLES) + index) * 6000
                await self.publish(
                    TranscriptEvent(
                        segment_id=segment_id,
                        version=1,
                        stage=CaptionStage.PARTIAL,
                        source_language=self._request.source_language,
                        text=raw[:-3],
                        start_ms=start_ms,
                        confidence=0.72,
                    )
                )
                await asyncio.sleep(0.55)
                await self.publish(
                    TranscriptEvent(
                        segment_id=segment_id,
                        version=2,
                        stage=CaptionStage.STABLE,
                        source_language=self._request.source_language,
                        text=raw,
                        start_ms=start_ms,
                        end_ms=start_ms + 5000,
                        confidence=0.93,
                    )
                )
                await asyncio.sleep(2.4)
            cycle += 1
