"""Revision epoch is preserved in every derived caption event."""

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline


def test_derived_caption_preserves_source_revision() -> None:
    source = TranscriptEvent(
        segment_id="revised", source_revision=3, version=7,
        stage=CaptionStage.STABLE, source_language="ja",
        text="恵み", start_ms=0, end_ms=900,
    )
    corrected = CaptionPipeline._next_event(
        source, version=8, stage=CaptionStage.CORRECTED, text="恵み",
    )
    translated = CaptionPipeline._next_event(
        corrected, version=9, stage=CaptionStage.TRANSLATED,
        text="恵み", translations={"ko": "은혜"},
        translation_states={"ko": "available"},
    )
    committed = CaptionPipeline._next_event(
        translated, version=10, stage=CaptionStage.COMMITTED,
        text="恵み", translations={"ko": "은혜"},
        translation_states={"ko": "available"}, committed=True,
    )
    assert [item.source_revision for item in (source, corrected, translated, committed)] == [
        3, 3, 3, 3,
    ]
    assert TranscriptEvent.model_validate(committed.model_dump()).source_revision == 3


def test_legacy_caption_payload_defaults_to_first_revision() -> None:
    source = TranscriptEvent.model_validate({
        "segment_id": "legacy", "version": 1, "stage": "stable",
        "source_language": "en", "text": "grace", "start_ms": 0,
    })
    assert source.source_revision == 1
