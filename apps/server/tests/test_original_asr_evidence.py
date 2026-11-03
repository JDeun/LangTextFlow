"""Correction must not replace the raw evidence retained for archives and summaries."""

import asyncio
import json

import pytest
from test_session_repository import _request, _state

from langtextflow.config import Settings
from langtextflow.exports import export_json
from langtextflow.models import CaptionStage, ProductPreset, SessionContext, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.post_event_summary import transcript_source
from langtextflow.session_repository import SessionRepository


@pytest.mark.asyncio
async def test_raw_asr_survives_correction_persistence_export_and_summarization(tmp_path):
    history = SessionRepository(str(tmp_path / "history.db"))
    history.initialize()
    history.create_session(_state(), _request())
    done = asyncio.Event()

    async def publish(event):
        history.upsert_segment("session-1", event)
        if event.committed:
            done.set()

    request = _request().model_copy(update={
        "correction_provider": "none", "translation_provider": "none",
        "context": SessionContext(preset=ProductPreset.CHURCH),
    })
    pipeline = CaptionPipeline(publish, Settings())
    await pipeline.start(request)
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="source", version=1, stage=CaptionStage.STABLE,
            source_language="ko", text="요한 보금", start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(done.wait(), 1)
    finally:
        await pipeline.stop()
    restored = SessionRepository(str(tmp_path / "history.db")).segments("session-1")
    assert restored[0].text == "요한복음"
    assert restored[0].original_text == "요한 보금"
    assert transcript_source(restored)[0] == "[source at 0s] {} 요한 보금"
    exported = json.loads(export_json(history.get_session("session-1"), restored))
    assert exported["segments"][0]["original_text"] == "요한 보금"
    # Editing the display track must not silently replace a summary's raw source.
    edited = restored[0].model_copy(update={"text": "LLM rewritten claim"})
    assert transcript_source([edited]) == transcript_source(restored)
