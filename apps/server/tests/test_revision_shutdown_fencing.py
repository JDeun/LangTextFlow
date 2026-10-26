"""Superseded source epochs must not finalize a newer caption on shutdown."""

import asyncio

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline


def stable(revision: int) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="same", source_revision=revision, version=1,
        stage=CaptionStage.STABLE, source_language="ko",
        text="은혜", start_ms=0,
    )


@pytest.mark.asyncio
async def test_stale_queue_item_does_not_publish_terminal_on_stop() -> None:
    pipeline = object.__new__(CaptionPipeline)
    pipeline._queue = asyncio.Queue(maxsize=128)
    pipeline._queue.put_nowait(stable(1))
    pipeline._worker_task = None
    pipeline._extra_workers = []
    pipeline._source_revisions = {"same": 2}
    pipeline._active_stable_sources = {"same"}
    pipeline._queue_admitted_at = {}
    pipeline._worker_progress = {}
    pipeline.expired_postprocess_segments = 0
    pipeline.llm_corrector = None
    pipeline.translator = None
    pipeline.request = None
    pipeline.settings = type("Settings", (), {"postprocess_drain_timeout_seconds": 0.1})()
    published = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)

    pipeline.publish = publish
    await pipeline.stop()
    assert published == []
    assert pipeline.expired_postprocess_segments == 0
    assert pipeline._active_stable_sources == {"same"}
