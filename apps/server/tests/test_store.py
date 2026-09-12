import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.store import CaptionStore


def event(version: int, stage: CaptionStage, *, committed: bool = False) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="seg-1",
        version=version,
        stage=stage,
        source_language="ko",
        text="테스트",
        start_ms=0,
        committed=committed,
    )


def test_segment_can_progress_through_stages() -> None:
    store = CaptionStore()
    store.apply(event(1, CaptionStage.PARTIAL))
    store.apply(event(2, CaptionStage.STABLE))
    store.apply(event(3, CaptionStage.CORRECTED))
    assert store.snapshot()[0].stage is CaptionStage.CORRECTED


def test_rejects_stale_version() -> None:
    store = CaptionStore()
    store.apply(event(2, CaptionStage.STABLE))
    with pytest.raises(ValueError, match="version"):
        store.apply(event(1, CaptionStage.STABLE))


def test_rejects_stage_regression() -> None:
    store = CaptionStore()
    store.apply(event(1, CaptionStage.CORRECTED))
    with pytest.raises(ValueError, match="backwards"):
        store.apply(event(2, CaptionStage.STABLE))


def test_committed_segment_is_immutable() -> None:
    store = CaptionStore()
    store.apply(event(1, CaptionStage.COMMITTED, committed=True))
    with pytest.raises(ValueError, match="committed"):
        store.apply(event(2, CaptionStage.COMMITTED, committed=True))
