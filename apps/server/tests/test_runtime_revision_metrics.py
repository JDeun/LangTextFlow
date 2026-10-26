"""Runtime metrics expose rejected ASR source revisions to operators."""

import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_runtime_metrics_report_changed_stable_revisions(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "revision.sqlite3")))
    await runtime.start(StartSessionRequest(
        engine="mock", source_language="ko", target_languages=["en"],
        translation_provider="none",
    ))
    original = TranscriptEvent(
        segment_id="revision", version=1, stage=CaptionStage.STABLE,
        source_language="ko", text="은혜", start_ms=0, end_ms=500,
    )
    try:
        await runtime.pipeline.ingest(original)
        for _ in range(100):
            if any(event.committed for event in runtime.store.snapshot()):
                break
            await asyncio.sleep(0.01)
        assert runtime.store.snapshot()[0].committed
        await runtime.pipeline.ingest(original.model_copy(update={
            "version": 8, "text": "은혜가 충만합니다",
        }))
        metrics = runtime.metrics_snapshot()
        assert metrics.postprocess_revised_asr_events_rejected == 1
        assert metrics.postprocess_duplicate_asr_events_dropped == 1
        assert runtime.store.snapshot()[0].text == "은혜"
    finally:
        await runtime.stop()
