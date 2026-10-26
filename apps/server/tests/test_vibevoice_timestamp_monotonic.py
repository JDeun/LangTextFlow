"""Deterministic receiver contract for VibeVoice estimated audio timestamps."""

import json

import pytest

from langtextflow.asr.vibevoice import VibeVoiceStreamingAsrEngine
from langtextflow.models import StartSessionRequest


class FakeSocket:
    def __init__(self, messages):
        self.messages = messages

    def __aiter__(self):
        async def stream():
            for message in self.messages:
                yield json.dumps(message)
        return stream()


@pytest.mark.asyncio
async def test_multiple_sidecar_segments_do_not_overlap_or_exceed_pcm() -> None:
    published = []

    async def publish(event):
        published.append(event)

    engine = VibeVoiceStreamingAsrEngine(publish, base_url="http://localhost:9000")
    engine._request = StartSessionRequest(
        source_language="ko", target_languages=["en"], engine="vibevoice",
    )
    engine._sample_rate = 16000
    engine._chunk_seconds = 2.0
    engine._transmitted_audio_bytes = 16000 * 4 * 3
    engine._ws = FakeSocket([
        {"text": "first"}, {"text": "second"},
        {"text": "third"}, {"text": "fourth"}, {"done": True},
    ])
    await engine._receive_loop()

    assert [event.text for event in published] == [
        "first", "second", "third", "fourth",
    ]
    assert published[0].start_ms == 0
    assert all(0 <= event.start_ms <= event.end_ms <= 3000 for event in published)
    assert all(
        earlier.end_ms <= later.start_ms
        for earlier, later in zip(published, published[1:])
    )
    assert published[-1].start_ms == 3000
    assert published[-1].end_ms == 3000
