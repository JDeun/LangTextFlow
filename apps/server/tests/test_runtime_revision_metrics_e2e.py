"""End-to-end telemetry for changed same-segment ASR results."""

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_runtime_counts_rejected_revision_without_rewriting_durable_caption(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "revisions.sqlite3")))
    state = await runtime.start(StartSessionRequest(
        engine="mock", source_language="ko", target_languages=["en"],
        translation_provider="demo",
    ))
    assert state.session_id is not None

    def stable(text: str, version: int) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id="revision-e2e", version=version,
            stage=CaptionStage.STABLE, source_language="ko",
            text=text, start_ms=0, end_ms=500,
        )

    try:
        await runtime.pipeline.ingest(stable("은혜", 1))
        for _ in range(100):
            if any(row.committed for row in runtime.store.snapshot()):
                break
            await asyncio.sleep(0.01)
        assert runtime.store.snapshot()[0].committed
        original = runtime.store.snapshot()[0]

        await runtime.pipeline.ingest(stable("은혜가 충만합니다", 10))
        await runtime.pipeline.ingest(stable("은혜", 11))
        snapshot = runtime.metrics_snapshot()
        assert snapshot.postprocess_revised_asr_events_rejected == 1
        assert snapshot.postprocess_duplicate_asr_events_dropped == 2
        assert runtime.store.snapshot() == [original]
    finally:
        await runtime.stop()

    stored = runtime.history.segments(state.session_id)
    assert len(stored) == 1
    assert stored[0].text == original.text
    assert stored[0].translations == original.translations
    assert stored[0].committed
