"""Operator alias edits cannot erase negative clauses or change scripture digits."""

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.correction import DeterministicCorrector
from langtextflow.models import (
    CaptionStage,
    GlossaryEntry,
    SessionContext,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.pipeline import CaptionPipeline
from langtextflow.store import CaptionStore


@pytest.mark.parametrize(("language", "text", "alias", "canonical"), [
    ("en", "We are not saved.", "not saved", "saved"),
    ("en", "We are saved.", "saved", "not saved"),
    ("en", "We do not doubt and are not saved.", "not saved", "saved"),
    ("en", "We can't doubt.", "can't doubt", "can doubt"),
    ("en", "We can’t doubt.", "can’t doubt", "can doubt"),
    ("en", "They are not saved but judged.", "not saved but judged", "saved but not judged"),
    ("en", "No A.", "No A", "A no"),
    ("ko", "우리는 안 믿는다.", "안 믿는다", "믿는다"),
    ("ko", "믿지 않았다.", "믿지 않았다", "믿었다"),
    ("ko", "구원이 없다.", "없다", "있다"),
    ("ja", "彼は信じない。", "信じない", "信じる"),
    ("ja", "信じません。", "信じません", "信じます"),
    ("ja", "救われぬ。", "救われぬ", "救われる"),
    ("ko", "믿지 않는다 그러나 사랑한다.", "믿지 않는다 그러나 사랑한다",
     "믿는다 그러나 사랑하지 않는다"),
    ("ja", "信じないが愛する。", "信じないが愛する", "信じるが愛さない"),
    ("en", "Read John 3:16.", "John 3:16", "John 3:17"),
    ("ko", "요한복음 3장 16절", "3장 16절", "3장 17절"),
    ("ja", "ヨハネ3章16節", "3章16節", "3章17節"),
])
def test_unsafe_operator_alias_keeps_the_original(language, text, alias, canonical):
    context = SessionContext(glossary=[GlossaryEntry(
        source_language=language, term=canonical, aliases=[alias],
    )])
    assert DeterministicCorrector().correct(text, context, source_language=language) == text


@pytest.mark.parametrize(("language", "text", "alias", "canonical", "expected"), [
    ("en", "We can't doubt.", "can't", "cannot", "We cannot doubt."),
    ("en", "The holy spirit did not leave in 3 days.", "holy spirit", "Holy Spirit",
     "The Holy Spirit did not leave in 3 days."),
    ("ko", "요한 보금을 믿지 않습니다 3:16", "요한 보금", "요한복음",
     "요한복음을 믿지 않습니다 3:16"),
    ("ja", "ヨハネ の 福音書を信じない 3:16", "ヨハネ の 福音書", "ヨハネ福音書",
     "ヨハネ福音書を信じない 3:16"),
])
def test_normal_aliases_preserve_existing_negative_clauses_and_digits(
    language, text, alias, canonical, expected,
):
    context = SessionContext(glossary=[GlossaryEntry(
        source_language=language, term=canonical, aliases=[alias],
    )])
    assert DeterministicCorrector().correct(text, context, source_language=language) == expected


@pytest.mark.asyncio
async def test_pipeline_retains_raw_source_and_only_applies_safe_aliases(tmp_path):
    store = CaptionStore()

    async def publish(event):
        store.apply(event)

    pipeline = CaptionPipeline(publish, Settings(database_path=str(tmp_path / "test.db")))
    context = SessionContext(glossary=[
        GlossaryEntry(source_language="en", term="saved", aliases=["not saved"]),
        GlossaryEntry(source_language="en", term="John 3:17", aliases=["John 3:16"]),
        GlossaryEntry(source_language="en", term="Holy Spirit", aliases=["holy spirit"]),
    ])
    source = "The holy spirit is not saved in John 3:16."
    await pipeline.start(StartSessionRequest(
        engine="mock", source_language="en", target_languages=["ko"],
        correction_provider="none", translation_provider="none", context=context,
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="protected-source", version=1, stage=CaptionStage.STABLE,
            source_language="en", text=source, start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(pipeline._queue.join(), 1)
        event = store.snapshot()[0]
        assert event.committed and event.original_text == source
        assert event.text == "The Holy Spirit is not saved in John 3:16."
        assert event.correction.deterministic_changed
        assert event.translation_states == {"ko": "unsupported"}
    finally:
        await pipeline.stop()
