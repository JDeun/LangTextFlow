"""Bounded first-snapshot restoration for the Rust-owned synthetic timer."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from langtextflow.config import Settings
from langtextflow.models import AsrProvenance, TranscriptEvent
from langtextflow.rust_runtime import RustRuntimeBridge


class Socket:
    def __init__(self, frames):
        self.frames = frames

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for frame in self.frames:
            yield json.dumps(frame)


def snapshot():
    event = TranscriptEvent(
        segment_id="demo-000-00", version=1, stage="partial", source_language="ko",
        text="합성 부분 원문", start_ms=0, confidence=.72,
        asr_provenance=AsrProvenance(provider="mock", timestamp_basis="synthetic"),
    ).model_dump(mode="json") | {
        "session_id": "owned-session", "session_generation": 1, "serial": 1,
    }
    return {"type": "snapshot", "session_id": "owned-session", "session_generation": 1,
            "serial": 1, "segments": [event]}


async def relay(frames, *, audio=False, max_segments=100):
    events = []

    async def publish(event):
        events.append(event)

    owner = RustRuntimeBridge(publish, Settings(max_segments=max_segments), asyncio.Semaphore(1),
                              accepts_audio=audio)
    owner.session_id = "owned-session"
    owner._process = SimpleNamespace(stdin=SimpleNamespace(close=lambda: None))
    await owner._relay(Socket(frames))
    return owner, events


async def test_initial_demo_snapshot_preserves_partial_once_and_serial_without_inference():
    first = snapshot()
    owner, events = await relay([first])
    assert len(events) == 1 and events[0].stage == "partial"
    assert events[0].original_text is events[0].end_ms is None
    assert events[0].asr_provenance.timestamp_basis == "synthetic" and owner._serial == 1
    _, repeated = await relay([first, first])
    assert len(repeated) == 1  # A second snapshot cannot replay public events.
    _, audio = await relay([first], audio=True)
    assert audio == []  # Preserve real-audio startup's existing empty-only rule.


async def test_configured_live_capacity_above_legacy_hundred_reaches_snapshot_parser():
    value = snapshot()
    first = value["segments"][0]
    value["segments"] = [first | {"segment_id": f"demo-{index:03d}-00", "serial": index + 1}
                         for index in range(101)]
    value["serial"] = 101
    owner, events = await relay([value], max_segments=150)
    assert len(events) == 101 and owner._serial == 101
    _, rejected = await relay([value])
    assert rejected == []


@pytest.mark.parametrize("change", [
    lambda value: value["segments"].append(copy.deepcopy(value["segments"][0])),
    lambda value: value["segments"].append(value["segments"][0] | {"session_id": "retired"}),
    lambda value: value["segments"][0].update(session_generation=True),
    lambda value: value["segments"][0].update(serial=2),
    lambda value: value["segments"][0].update(type="control"),
    lambda value: value["segments"][0]["asr_provenance"].update(provider="faster-whisper"),
    lambda value: value["segments"][0]["asr_provenance"].update(timestamp_basis="native_aligned"),
    lambda value: value.update(segments=value["segments"] * 101),
])
async def test_invalid_demo_snapshot_is_rejected_before_any_mirror_mutation(change):
    first = snapshot()
    change(first)
    owner, events = await relay([first])
    assert owner.failure is not None and not events and owner._serial == 0
