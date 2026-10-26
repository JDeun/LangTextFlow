"""Exercise six local-MT directions through the actual caption pipeline wiring.

Native weights are mocked; this is not a model-quality benchmark.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "targets"),
    [
        ("ko", ["en", "ja"]),
        ("en", ["ko", "ja"]),
        ("ja", ["ko", "en"]),
    ],
)
async def test_local_mt_six_directions_reach_committed_tracks(
    source: str, targets: list[str],
) -> None:
    published: list[TranscriptEvent] = []
    committed = asyncio.Event()
    translated_calls: list[tuple[str, str]] = []

    async def publish(event: TranscriptEvent) -> None:
        published.append(event)
        if event.stage is CaptionStage.COMMITTED:
            committed.set()

    async def prepare(self: M2M100CT2Translator) -> None:
        self._engine = object()
        self._tokenizer = object()

    def infer(
        self: M2M100CT2Translator,
        text: str, src: str, target: str, tokenizer: object, engine: object,
    ) -> str:
        del self, text, tokenizer, engine
        translated_calls.append((src, target))
        return f"translation-{src}-{target}"

    pipeline = CaptionPipeline(publish, Settings())
    with (
        patch.object(M2M100CT2Translator, "prepare", prepare),
        patch.object(M2M100CT2Translator, "_translate_sync", infer),
    ):
        await pipeline.start(StartSessionRequest(
            source_language=source,
            target_languages=targets,
            translation_provider="m2m100-ct2",
        ))
        try:
            await pipeline.ingest(TranscriptEvent(
                segment_id=f"segment-{source}",
                version=1,
                stage=CaptionStage.STABLE,
                source_language=source,
                text="A source caption for testing",
                start_ms=0,
                end_ms=1000,
            ))
            await asyncio.wait_for(committed.wait(), timeout=3)
        finally:
            await pipeline.stop()

    assert sorted(translated_calls) == sorted((source, target) for target in targets)
    assert [event.stage for event in published] == [
        CaptionStage.STABLE,
        CaptionStage.CORRECTED,
        CaptionStage.TRANSLATED,
        CaptionStage.TRANSLATED,
        CaptionStage.COMMITTED,
    ]
    assert [event.version for event in published] == [1, 2, 3, 4, 5]
    terminal = published[-1]
    assert terminal.committed
    assert terminal.text == "A source caption for testing"
    assert terminal.translation_states == dict.fromkeys(targets, "available")
    assert terminal.translations == {
        target: f"translation-{source}-{target}" for target in targets
    }
    for event in published:
        assert event.start_ms == 0
        assert event.end_ms == 1000
