"""Contract tests for immutable caption versions and future ASR revision handling."""

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.store import CaptionStore


def event(
    version: int,
    stage: CaptionStage,
    text: str,
    *,
    translations: dict[str, str] | None = None,
    states: dict[str, str] | None = None,
    committed: bool = False,
) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="revision", version=version, stage=stage,
        source_language="ko", text=text, start_ms=0, end_ms=500,
        translations=translations or {},
        translation_states=states or {},
        committed=committed,
    )


def test_committed_segment_cannot_be_reopened_by_later_source_revision() -> None:
    store = CaptionStore()
    committed = event(
        4, CaptionStage.COMMITTED, "은혜",
        translations={"en": "Grace"}, states={"en": "available"},
        committed=True,
    )
    store.apply(committed)
    with pytest.raises(ValueError, match="already committed"):
        store.apply(event(5, CaptionStage.STABLE, "은혜가 충만합니다"))
    assert store.snapshot() == [committed]


def test_inflight_revision_cannot_erase_available_target() -> None:
    store = CaptionStore()
    translated = event(
        3, CaptionStage.TRANSLATED, "은혜",
        translations={"en": "Grace"}, states={"en": "available", "ja": "pending"},
    )
    store.apply(translated)
    with pytest.raises(ValueError, match="stage cannot move backwards"):
        store.apply(event(4, CaptionStage.STABLE, "은혜가 충만합니다"))
    with pytest.raises(ValueError, match="published translation cannot regress"):
        store.apply(event(
            4, CaptionStage.TRANSLATED, "은혜가 충만합니다",
            translations={"en": "A different translation"},
            states={"en": "available", "ja": "pending"},
        ))
    assert store.snapshot() == [translated]


def test_progressive_target_update_keeps_first_language_immutable() -> None:
    store = CaptionStore()
    first = event(
        3, CaptionStage.TRANSLATED, "은혜",
        translations={"en": "Grace"},
        states={"en": "available", "ja": "pending"},
    )
    store.apply(first)
    second = event(
        4, CaptionStage.TRANSLATED, "은혜",
        translations={"en": "Grace", "ja": "恵み"},
        states={"en": "available", "ja": "available"},
    )
    store.apply(second)
    assert store.snapshot() == [second]
