import pytest
from pydantic import ValidationError

from langtextflow.models import (
    MAX_GLOSSARY_ALIASES,
    MAX_HOTWORDS,
    MAX_TARGET_LANGUAGES,
    MAX_TRANSCRIPT_TEXT_CHARS,
    MAX_TRANSLATION_TEXT_CHARS,
    CaptionStage,
    GlossaryEntry,
    SessionContext,
    StartSessionRequest,
    TranscriptEvent,
)


def test_session_context_rejects_hotword_cardinality_and_length() -> None:
    with pytest.raises(ValidationError):
        SessionContext(hotwords=[f"term-{index}" for index in range(MAX_HOTWORDS + 1)])
    with pytest.raises(ValidationError, match="hotword exceeds"):
        SessionContext(hotwords=["x" * 161])


def test_glossary_rejects_alias_cardinality() -> None:
    with pytest.raises(ValidationError):
        GlossaryEntry(
            term="term",
            aliases=[f"alias-{index}" for index in range(MAX_GLOSSARY_ALIASES + 1)],
        )


def test_start_request_rejects_target_language_explosion() -> None:
    targets = [f"x{index:02d}" for index in range(MAX_TARGET_LANGUAGES + 1)]
    with pytest.raises(ValidationError):
        StartSessionRequest(source_language="ko", target_languages=targets)


def test_transcript_event_rejects_oversized_source_and_translation() -> None:
    base = {
        "segment_id": "seg-1",
        "version": 1,
        "stage": CaptionStage.STABLE,
        "source_language": "ko",
        "start_ms": 0,
        "end_ms": 1000,
    }
    with pytest.raises(ValidationError):
        TranscriptEvent(**base, text="x" * (MAX_TRANSCRIPT_TEXT_CHARS + 1))
    with pytest.raises(ValidationError, match="translation text exceeds"):
        TranscriptEvent(
            **base,
            text="valid",
            translations={"en": "x" * (MAX_TRANSLATION_TEXT_CHARS + 1)},
        )
