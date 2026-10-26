"""Obsolete source epochs cannot publish postprocess events."""

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline


def event(revision: int) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="same", source_revision=revision, version=3,
        stage=CaptionStage.TRANSLATED, source_language="ko",
        text="은혜", translations={"en": "grace"},
        translation_states={"en": "available"}, start_ms=0,
    )


@pytest.mark.asyncio
async def test_obsolete_worker_result_is_not_published() -> None:
    published: list[TranscriptEvent] = []

    async def publish(item: TranscriptEvent) -> None:
        published.append(item)

    pipeline = object.__new__(CaptionPipeline)
    pipeline.publish = publish
    pipeline._source_revisions = {"same": 2}
    pipeline._worker_progress = {}

    await pipeline._publish_postprocess(event(1))
    assert published == []
    assert pipeline._worker_progress == {}

    await pipeline._publish_postprocess(event(2))
    assert [item.source_revision for item in published] == [2]
    assert pipeline._worker_progress["same"].source_revision == 2


@pytest.mark.asyncio
async def test_progress_does_not_regress_when_epoch_changes_during_publish() -> None:
    pipeline = object.__new__(CaptionPipeline)
    pipeline._source_revisions = {"same": 1}
    pipeline._worker_progress = {}

    async def publish(_item: TranscriptEvent) -> None:
        pipeline._source_revisions["same"] = 2

    pipeline.publish = publish
    await pipeline._publish_postprocess(event(1))
    assert pipeline._worker_progress == {}
