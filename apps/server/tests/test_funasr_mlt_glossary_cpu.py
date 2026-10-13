"""FunASR CPU glossary A/B logic without model loads or external downloads."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.benchmark_funasr_mlt_glossary_cpu import (
    MODEL_ID,
    SOURCE_TO_FUNASR,
    asr_hotwords,
    decode,
    snapshot_hashes,
)
from scripts.glossary_cpu_contract import load_glossary_snapshot

GLOSSARY = Path("benchmarks/church_glossary_multilingual.v1.json")


def test_checkpoint_is_multilingual_not_standard_nano() -> None:
    assert MODEL_ID == "FunAudioLLM/Fun-ASR-MLT-Nano-2512"
    assert set(SOURCE_TO_FUNASR) == {"ko", "en", "ja"}


def test_hotwords_are_selected_before_any_audio_reference_exists() -> None:
    rows, _ = load_glossary_snapshot(GLOSSARY)
    ko = asr_hotwords(rows, "ko")
    en = asr_hotwords(rows, "en")
    ja = asr_hotwords(rows, "ja")
    assert "성령" in ko and "Holy Spirit" not in ko
    assert "Holy Spirit" in en and "성령" not in en
    assert "聖霊" in ja and "Holy Spirit" not in ja
    assert len(ko) == len(en) == len(ja) == 3
    assert asr_hotwords(rows, "en", preset="lecture") == []
    assert asr_hotwords(rows, "en", limit=0) == []


def test_disabled_aliases_source_scope_preset_and_bounds() -> None:
    entries = [
        {
            "term": "Grace",
            "source_language": "en",
            "aliases": ["the grace", "GRACE"],
            "presets": ["church"],
            "enabled": True,
            "boost": 2,
        },
        {
            "term": "Hidden", "source_language": "en", "enabled": False,
        },
        {
            "term": "Mercy", "source_language": "ja", "enabled": True,
        },
    ]
    assert asr_hotwords(entries, "en") == ["Grace", "the grace"]
    assert asr_hotwords(entries, "ko") == []
    assert asr_hotwords(entries, "en", limit=1) == ["Grace"]
    with pytest.raises(ValueError, match="unsupported"):
        asr_hotwords(entries, "fr")
    with pytest.raises(ValueError, match="unsupported"):
        asr_hotwords(entries, "en", limit=25)


class FakeASR:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"text": " The Holy Spirit is with us. "}]


def test_decode_passes_real_hotwords_to_model_without_fake_correction() -> None:
    model = FakeASR()
    base, duration_base = decode(
        model, Path("/tmp/speech.wav"), "en", hotwords=[]
    )
    boosted, duration_boosted = decode(
        model, Path("/tmp/speech.wav"), "en",
        hotwords=["Holy Spirit"],
    )
    assert boosted == base == "The Holy Spirit is with us."
    assert duration_base >= 0 and duration_boosted >= 0
    assert model.calls[0]["hotwords"] == []
    assert model.calls[1]["hotwords"] == ["Holy Spirit"]
    assert model.calls[0]["language"] == "英文"
    assert model.calls[1]["language"] == "英文"
    assert model.calls[0]["llm_kwargs"] == {"do_sample": False}
    assert model.calls[0]["input"] == model.calls[1]["input"]


def test_decode_does_not_turn_bad_shape_into_empty_success() -> None:
    class Bad:
        def generate(self, **kwargs):
            return [{"unexpected": "not a transcript"}]

    with pytest.raises(RuntimeError, match="missing text"):
        decode(Bad(), Path("/tmp/speech.wav"), "ko", hotwords=[])


def test_binary_model_digest_is_streamed_and_relative(tmp_path: Path) -> None:
    path = tmp_path / "model.pt"
    path.write_bytes(b"fake-test-weights")
    hashes = snapshot_hashes(tmp_path)
    assert hashes == {"model.pt": hashlib.sha256(b"fake-test-weights").hexdigest()}
