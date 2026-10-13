"""Never compare different source recordings or placeholder transcripts."""
from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.compare_cpu_asr_candidates import aggregate


def make_report(name: str = "sensevoice") -> dict:
    return {
        "status": "measured", "real_model": True, "candidate": name,
        "dataset": "google/fleurs", "source_language": "ko",
        "revision": "fdd74f7cdf01fc7641ab3072b6a38d364b3667f6",
        "mean_end_to_end_rtf": 0.17,
        "resources": {"rss_peak_mb": 300},
        "samples": [
            {
                "id": index, "pcm_sha256": "sha" + str(index),
                "reference": "예배 안내 " + str(index),
                "duration_seconds": 8 + index, "hypothesis": "예배 안내",
                "streaming_kind": "offline_utterance",
                "quality": {"cer": 0.1 * index, "wer": 0.2 * index},
            } for index in (1, 2, 3)
        ],
    }


def test_only_matching_real_audio_can_be_ranked() -> None:
    base = make_report()
    challenger = make_report("zipformer-ko")
    challenger["samples"][0]["quality"]["cer"] = 0
    result = aggregate([base, challenger])
    assert result["candidate_count"] == 2
    assert result["ranked"][0]["candidate"] == "zipformer-ko"
    assert result["field_ready"] is False


@pytest.mark.parametrize("mismatch", ["sha", "reference", "duration", "order", "revision"])
def test_strict_comparability_failure(mismatch: str) -> None:
    base = make_report()
    bad = deepcopy(make_report("qwen3-0.6b"))
    if mismatch == "sha":
        bad["samples"][0]["pcm_sha256"] = "different"
    elif mismatch == "reference":
        bad["samples"][0]["reference"] += "추가"
    elif mismatch == "duration":
        bad["samples"][0]["duration_seconds"] += 1
    elif mismatch == "order":
        bad["samples"].reverse()
    else:
        bad["revision"] = "other"
    with pytest.raises(ValueError):
        aggregate([base, bad])


def test_unverified_placeholder_is_refused() -> None:
    bad = make_report()
    bad["real_model"] = False
    with pytest.raises(ValueError, match="synthetic"):
        aggregate([bad])


def test_missing_results_do_not_imply_success() -> None:
    assert aggregate([])["status"] == "no-measured-models"
    assert aggregate([])["field_ready"] is False


def test_repeated_candidate_not_double_counted() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        aggregate([make_report(), make_report()])
