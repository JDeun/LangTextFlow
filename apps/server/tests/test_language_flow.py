import pytest
from pydantic import ValidationError

from langtextflow.models import StartSessionRequest


def test_arbitrary_source_can_translate_to_multiple_targets() -> None:
    request = StartSessionRequest(
        source_language="ja",
        target_languages=["ko", "en", "ko"],
    )

    assert request.source_language == "ja"
    assert request.target_languages == ["ko", "en"]


def test_source_language_cannot_also_be_translation_target() -> None:
    with pytest.raises(ValidationError, match="source language is always the original track"):
        StartSessionRequest(
            source_language="en",
            target_languages=["ko", "en"],
        )


def test_foreign_language_to_korean_is_valid() -> None:
    request = StartSessionRequest(
        source_language="en",
        target_languages=["ko"],
    )

    assert request.target_languages == ["ko"]
