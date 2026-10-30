"""Active source revision cancels old correction and starts replacement."""

import asyncio
from collections import OrderedDict

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore


def stable(revision: int, version: int, text: str) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="active", source_revision=revision, version=version,
        stage=CaptionStage.STABLE, source_language="ko", text=text, start_ms=0,
    )


@pytest.mark.asyncio
async def test_active_source_revision_cancels_inference_and_requeues() -> None:
    store = CaptionStore()
    old = stable(1, 1, "이전 원문")
    store.apply(old)
    started = asyncio.Event()

    async def processing() -> None:
        started.set()
        await asyncio.Event().wait()

    pipeline = object.__new__(CaptionPipeline)
    pipeline.publish = lambda event: publish(store, event)
    pipeline._source_revisions = {"active": 1}
    pipeline._processing_segments = {"active"}
    pipeline._processing_tasks = {"active": asyncio.create_task(processing())}
    pipeline._revision_locks = {}
    pipeline._superseding_segments = set()
    pipeline._seen_stable_sources = OrderedDict([("active", None)])
    pipeline._stable_source_text = OrderedDict([("active", old.text)])
    pipeline._active_stable_sources = {"active"}
    pipeline._queue = asyncio.Queue(maxsize=128)
    pipeline._queue_admitted_at = {}
    pipeline._worker_progress = {}
    pipeline.revised_asr_events_rejected = 0
    pipeline.dropped_postprocess_segments = 0
    pipeline.request = None
    await started.wait()

    await pipeline.ingest(stable(2, 2, "새 원문"))
    assert pipeline._processing_tasks["active"].cancelled()
    assert store.snapshot()[0].text == "새 원문"
    assert store.snapshot()[0].source_revision == 2
    assert pipeline._queue.get_nowait().source_revision == 2


async def publish(store: CaptionStore, event: TranscriptEvent) -> None:
    store.apply(event)
