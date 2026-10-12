"""Prevent misleading ASR comparisons across different licensed speech clips."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[3] / "scripts" / "compare_fleurs_runs.py"
SPEC = spec_from_file_location("fleurs_compare", FILE)
assert SPEC is not None and SPEC.loader is not None
compare_module = module_from_spec(SPEC)
SPEC.loader.exec_module(compare_module)


def run(window: int, *, cer: float = 1.5) -> dict:
    return {
        "dataset": "google/fleurs", "revision": "a" * 40,
        "split": "test", "language": "ko", "model": "tiny",
        "decode_chunk_seconds": window,
        "samples": [
            {"id": 1883, "sha256_pcm16": "b" * 64,
             "reference_characters": 69, "audio_duration_seconds": 12.48,
             "cer": cer, "wer": 1.1, "throughput_rtf": 0.8},
        ],
    }


def test_equivalent_audio_supports_quality_and_speed_comparison() -> None:
    comparison = compare_module.compare(run(4), run(30, cer=0.25))
    assert comparison["status"] == "comparable"
    assert comparison["baseline_config"]["chunk_seconds"] == 4
    assert comparison["candidate_config"]["chunk_seconds"] == 30
    assert comparison["aggregate"]["mean_cer"]["change"] == -1.25
    assert comparison["per_sample"][0]["id"] == "1883"


@pytest.mark.parametrize("mutation", ["revision", "language", "id", "sha256_pcm16", "reference_characters", "audio_duration_seconds"])
def test_mismatched_audio_or_reference_is_rejected(mutation: str) -> None:
    candidate = run(30)
    if mutation in ("revision", "language"):
        candidate[mutation] = "different"
    elif mutation == "id":
        candidate["samples"][0]["id"] = 1984
    elif mutation == "audio_duration_seconds":
        candidate["samples"][0][mutation] = 11.8
    else:
        candidate["samples"][0][mutation] = "other"
    with pytest.raises(ValueError, match="non-comparable"):
        compare_module.compare(run(4), candidate)


def test_empty_samples_and_duplicate_ids_are_rejected() -> None:
    candidate = run(30)
    candidate["samples"] = []
    with pytest.raises(ValueError, match="speech samples"):
        compare_module.compare(run(4), candidate)
    candidate = run(30)
    candidate["samples"].append(candidate["samples"][0].copy())
    with pytest.raises(ValueError, match="duplicate"):
        compare_module.compare(run(4), candidate)
