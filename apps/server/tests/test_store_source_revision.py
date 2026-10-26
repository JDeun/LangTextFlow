"""Explicit source revision epochs are isolated from target snapshots."""

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.store import CaptionStore


def event(revision: int, version: int, stage: CaptionStage, **kwargs) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="revision-1", source_revision=revision, version=version,
        stage=stage, source_language="ko", text=f"source {revision}",
        start_ms=0, end_ms=300, **kwargs,
    )


def test_new_revision_invalidates_prior_target_without_reusing_old_translation() -> None:
    store = CaptionStore()
    store.apply(event(1, 1, CaptionStage.STABLE))
    store.apply(event(
        1, 2, CaptionStage.TRANSLATED,
        translations={"en": "old"}, translation_states={"en": "available"},
    ))
    store.apply(event(2, 3, CaptionStage.STABLE))
    current = store.snapshot()[0]
    assert current.source_revision == 2
    assert current.translations == {}
    assert current.translation_states == {}
    store.apply(event(
        2, 4, CaptionStage.TRANSLATED,
        translations={"en": "new"}, translation_states={"en": "available"},
    ))
    with pytest.raises(ValueError, match="source revision cannot regress"):
        store.apply(event(1, 5, CaptionStage.COMMITTED, committed=True))
    assert store.snapshot()[0].translations == {"en": "new"}


@pytest.mark.parametrize("revision,version,stage,extra", [
    (3, 3, CaptionStage.STABLE, {}),
    (2, 2, CaptionStage.STABLE, {}),
    (2, 3, CaptionStage.TRANSLATED, {}),
    (2, 3, CaptionStage.STABLE, {"translations": {"en": "stale"}}),
])
def test_revision_requires_sequential_clean_stable_boundary(
    revision: int, version: int, stage: CaptionStage, extra: dict,
) -> None:
    store = CaptionStore()
    store.apply(event(1, 2, CaptionStage.CORRECTED))
    with pytest.raises(ValueError):
        store.apply(event(revision, version, stage, **extra))


def test_committed_segment_stays_immutable_across_revision_epochs() -> None:
    store = CaptionStore()
    store.apply(event(1, 1, CaptionStage.COMMITTED, committed=True))
    with pytest.raises(ValueError, match="already committed"):
        store.apply(event(2, 2, CaptionStage.STABLE))
