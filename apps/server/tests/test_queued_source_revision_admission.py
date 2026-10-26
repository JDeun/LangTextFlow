"""Queued ASR source revisions are admitted without launching stale inference."""

import asyncio

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore


def stable(revision: int, version: int, text: str) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="utterance", source_revision=revision, version=version,
        stage=CaptionStage.STABLE, source_language="ko", text=text, start_ms=0,
    )


@pytest.mark.asyncio
async def test_sequential_revision_replaces_queued_source_and_skips_old_work() -> None:
    store = CaptionStore()
    pipeline = object.__new__(CaptionPipeline)
    pipeline.publish = lambda event: publish(store, event)
    pipeline._source_revisions = {}
    pipeline._processing_segments = set()
    pipeline._processing_tasks = {}
    pipeline._revision_locks = {}
    pipeline._superseding_segments = set()
    pipeline._seen_stable_sources = __import__("collections").OrderedDict()
    pipeline._stable_source_text = __import__("collections").OrderedDict()
    pipeline._active_stable_sources = set()
    pipeline._queue = asyncio.Queue(maxsize=128)
    pipeline._queue_admitted_at = {}
    pipeline._worker_progress = {}
    pipeline.duplicate_asr_events_dropped = 0
    pipeline.revised_asr_events_rejected = 0
    pipeline.dropped_postprocess_segments = 0
    pipeline.request = None

    await pipeline.ingest(stable(1, 1, "원본"))
    await pipeline.ingest(stable(2, 2, "수정본"))
    assert store.snapshot()[0].source_revision == 2
    assert store.snapshot()[0].text == "수정본"
    assert pipeline._queue.qsize() == 2
    assert pipeline._source_revisions["utterance"] == 2
    await pipeline.ingest(stable(1, 3, "원본"))
    assert pipeline._queue.qsize() == 2
    assert pipeline.revised_asr_events_rejected == 1


@pytest.mark.asyncio
async def test_active_worker_revision_is_not_admitted() -> None:
    store = CaptionStore()
    pipeline = object.__new__(CaptionPipeline)
    pipeline.publish = lambda event: publish(store, event)
    pipeline._source_revisions = {"utterance": 1}
    pipeline._processing_segments = {"utterance"}
    pipeline._processing_tasks = {}
    pipeline._revision_locks = {}
    pipeline._superseding_segments = set()
    pipeline._seen_stable_sources = __import__("collections").OrderedDict(
        [("utterance", None)]
    )
    pipeline._stable_source_text = __import__("collections").OrderedDict(
        [("utterance", "원본")]
    )
    pipeline._active_stable_sources = {"utterance"}
    pipeline._queue = asyncio.Queue(maxsize=128)
    pipeline._queue_admitted_at = {}
    pipeline.duplicate_asr_events_dropped = 0
    pipeline.revised_asr_events_rejected = 0
    pipeline.dropped_postprocess_segments = 0
    pipeline.request = None
    store.apply(stable(1, 1, "원본"))

    await pipeline.ingest(stable(2, 2, "수정본"))
    assert store.snapshot()[0].source_revision == 1
    assert pipeline.revised_asr_events_rejected == 1


async def publish(store: CaptionStore, event: TranscriptEvent) -> None:
    store.apply(event)
