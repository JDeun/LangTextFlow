"""Immutable host configuration; these checks do not import/load native models."""
import sys

import pytest

from langtextflow.config import Settings
from langtextflow.models import StartSessionRequest
from langtextflow.runtime import CaptionRuntime
from langtextflow.rust_runtime import _audio_options, _whisper_options


def request(**changes):
    return StartSessionRequest(engine="faster-whisper", source_language="en",
                               target_languages=["ko"], translation_provider="none", **changes)


def test_legacy_audio_defaults_and_largest_bounded_configuration():
    assert _audio_options(Settings()) == {
        "protocol_version": 1, "chunk_samples": 64000, "vad_threshold_dbfs": -45.,
        "vad_hangover_frames": 3, "max_frame_bytes": 1024 * 1024, "max_segments": 100,
    }
    settings = Settings(faster_whisper_chunk_seconds=120, vad_threshold_dbfs=-63.,
                        vad_hangover_frames=10000, max_audio_frame_bytes=8 * 1024 * 1024,
                        max_segments=10000)
    assert _audio_options(settings)["chunk_samples"] == 1920000
    assert _audio_options(settings)["max_frame_bytes"] == 8388608
    assert _audio_options(Settings(faster_whisper_chunk_seconds=.000001))["chunk_samples"] == 1


@pytest.mark.parametrize("server_low,request_low", [(False, False), (True, False), (False, True)])
def test_effective_low_overrides_only_whisper_device_and_compute(server_low, request_low):
    settings = Settings(resource_profile="low" if server_low else "standard",
                        faster_whisper_device="cuda", faster_whisper_compute_type="float16")
    profile = "low" if request_low else "standard"
    value = _whisper_options(settings, request(resource_profile=profile))
    assert value == {"protocol_version": 1,
                     "device": "cpu" if server_low or request_low else "cuda",
                     "compute_type": "int8" if server_low or request_low else "float16"}
    for engine in ("mock", "vibevoice"):
        assert _whisper_options(settings, request().model_copy(update={"engine": engine})) is None


@pytest.mark.parametrize("change", [
    {"faster_whisper_device": "private/endpoint"},
    {"faster_whisper_compute_type": "unknown"},
    {"faster_whisper_chunk_seconds": float("nan")},
    {"faster_whisper_chunk_seconds": .00008},  # Five bytes is not a complete float32 window.
    {"faster_whisper_chunk_seconds": 120.1},
    {"vad_threshold_dbfs": float("inf")}, {"vad_hangover_frames": 10001},
    {"max_audio_frame_bytes": 8388612}, {"max_audio_frame_bytes": 7}, {"max_segments": 10001},
])
async def test_invalid_configuration_is_refused_before_history_or_runtime_mutation(
    tmp_path, change,
):
    database = tmp_path / "must-not-create.db"
    settings = Settings(runtime_core="rust", rust_runtime_binary=sys.executable,
                        database_path=str(database)).model_copy(update=change)
    runtime = CaptionRuntime(settings)
    with pytest.raises(ValueError):
        await runtime.start(request())
    assert not runtime._initialized and runtime.engine is None and not database.exists()
