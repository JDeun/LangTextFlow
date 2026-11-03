"""Fail fast before optional dataset/model downloads in manual ASR evaluation."""
import io
import json
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

# The optional evaluation helper is a standalone script, not a Python package.
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "evaluate_fleurs_asr.py"
SPEC = spec_from_file_location("fleurs_evaluation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
helper = module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
CONFIGS = helper.CONFIGS
FLEURS_LICENSE = helper.FLEURS_LICENSE
FLEURS_REVISION = helper.FLEURS_REVISION
evaluate = helper.evaluate
WINDOW_SECONDS = helper.WINDOW_SECONDS


def test_fixture_source_is_pinned_and_attributed() -> None:
    assert len(FLEURS_REVISION) == 40
    assert all(value in "0123456789abcdef" for value in FLEURS_REVISION)
    assert FLEURS_LICENSE == "https://creativecommons.org/licenses/by/4.0/"
    assert CONFIGS == {"ko": "ko_kr", "en": "en_us"}


@pytest.mark.parametrize(
    ("language", "samples", "model", "message"),
    [
        ("fr", 1, "tiny", "language must be"),
        ("ko", 0, "tiny", "samples must be"),
        ("en", 9, "tiny", "samples must be"),
        ("ko", 1, "unavailable", "CPU model must be"),
    ],
)
def test_invalid_config_rejected_without_dataset_download(
    tmp_path, language: str, samples: int, model: str, message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate(language, samples, tmp_path / "evaluation", model=model)
    assert not (tmp_path / "evaluation").exists()


def test_pinned_parquet_source_avoids_legacy_dataset_scripts() -> None:
    for language, config in CONFIGS.items():
        uri = helper.fleurs_test_parquet_uri(language)
        assert uri == (
            f"hf://datasets/google/fleurs@{FLEURS_REVISION}/"
            f"{config}/test-00000-of-00001.parquet"
        )
        assert "fleurs.py" not in uri
    with pytest.raises(ValueError, match="language must be"):
        helper.fleurs_test_parquet_uri("unknown")


def test_audio_source_prefers_pinned_bytes_over_path(tmp_path) -> None:
    source = helper.audio_input({
        "bytes": b"fixture-audio", "path": str(tmp_path / "missing.wav"),
    })
    assert isinstance(source, io.BytesIO)
    assert source.read() == b"fixture-audio"


def test_audio_source_accepts_local_streaming_cache_path(tmp_path) -> None:
    cache = tmp_path / "fleurs.wav"
    cache.write_bytes(b"local-fixture")
    assert helper.audio_input({"bytes": None, "path": str(cache)}) == cache


@pytest.mark.parametrize(
    "audio",
    [
        {"path": "https://other-site.example/recording.wav", "bytes": None},
        {"path": "http://localhost/private", "bytes": None},
        {"path": "/path/that/does/not/exist.wav", "bytes": None},
        {"path": None, "bytes": None},
    ],
)
def test_audio_source_does_not_fetch_untrusted_remote_paths(audio) -> None:
    with pytest.raises(ValueError, match="local readable file"):
        helper.audio_input(audio)


def test_asr_chunk_window_is_reproducible_and_rejects_invalid_values(tmp_path) -> None:
    assert WINDOW_SECONDS == (4, 15, 30)
    for invalid in (0, 1, 3, 31, 120):
        with pytest.raises(ValueError, match="chunk_seconds"):
            evaluate("ko", 1, tmp_path / "benchmark", chunk_seconds=invalid)
    assert not (tmp_path / "benchmark").exists()


@pytest.mark.parametrize("timed_out", [False, True])
def test_failed_inference_retains_provenance_and_removes_raw_fixtures(
    monkeypatch, tmp_path, timed_out,
):
    class Dataset(list):
        def cast_column(self, *args):
            return self

    monkeypatch.setitem(sys.modules, "datasets", SimpleNamespace(
        Audio=lambda **kwargs: None,
        load_dataset=lambda *args, **kwargs: Dataset([
            {"id": 42, "raw_transcription": "independent reference", "audio": {}},
        ]),
    ))

    def write_wav(audio, destination):
        destination.write_bytes(b"synthetic private fixture")
        return 1.0, "a" * 64

    def run(command, **kwargs):
        if timed_out:
            raise subprocess.TimeoutExpired(command, 240, stderr=b"bounded stderr")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="invalid ASR timestamps")

    monkeypatch.setattr(helper, "write_pcm_wav", write_wav)
    monkeypatch.setattr(helper.subprocess, "run", run)
    output = tmp_path / "evaluation"
    with pytest.raises(RuntimeError, match="ASR benchmark failed"):
        evaluate("ko", 1, output)
    failure = json.loads((output / "failure-000.json").read_text())
    assert failure["status"] == "error"
    assert failure["dataset_revision"] == FLEURS_REVISION
    assert failure["sha256_pcm16"] == "a" * 64
    assert failure["sample_id"] == 42
    assert failure["returncode"] == (124 if timed_out else 1)
    assert list((output / "temporary-fixtures").iterdir()) == []
