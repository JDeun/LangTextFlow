"""Exercise the shared worker across a live ASR source revision."""

import asyncio
from collections import OrderedDict

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore


def stable(revision: int, text: str) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="same", version=revision, source_revision=revision,
        stage=CaptionStage.STABLE, source_language="ko", text=text, start_ms=0,
    )


@pytest.mark.asyncio
async def test_worker_survives_active_revision_and_processes_new_epoch() -> None:
    store = CaptionStore()
    entered = asyncio.Event()
    completed = asyncio.Event()
    processed: list[int] = []

    async def publish(event: TranscriptEvent) -> None:
        store.apply(event)

    async def process(event: TranscriptEvent) -> None:
        processed.append(event.source_revision)
        if event.source_revision == 1:
            entered.set()
            await asyncio.Event().wait()
        completed.set()

    pipeline = object.__new__(CaptionPipeline)
    pipeline.publish = publish
    pipeline._process_stable = process
    pipeline._source_revisions = {}
    pipeline._processing_segments = set()
    pipeline._processing_tasks = {}
    pipeline._revision_locks = {}
    pipeline._superseding_segments = set()
    pipeline._seen_stable_sources = OrderedDict()
    pipeline._stable_source_text = OrderedDict()
    pipeline._active_stable_sources = set()
    pipeline._queue = asyncio.Queue(maxsize=16)
    pipeline._queue_admitted_at = {}
    pipeline._queue_lag_ms = []
    pipeline._worker_progress = {}
    pipeline.duplicate_asr_events_dropped = 0
    pipeline.revised_asr_events_rejected = 0
    pipeline.dropped_postprocess_segments = 0
    pipeline.expired_postprocess_segments = 0
    pipeline.failed_postprocess_segments = 0
    pipeline.request = None

    await pipeline.ingest(stable(1, "첫 원문"))
    worker = asyncio.create_task(pipeline._worker())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(pipeline.ingest(stable(2, "수정 원문")), 2)
        await asyncio.wait_for(completed.wait(), 2)
        assert processed == [1, 2]
        assert store.snapshot()[0].source_revision == 2
        assert store.snapshot()[0].text == "수정 원문"
        assert not worker.done()
        assert pipeline.expired_postprocess_segments == 0
    finally:
        await pipeline._queue.put(None)
        await asyncio.wait_for(worker, 2)
