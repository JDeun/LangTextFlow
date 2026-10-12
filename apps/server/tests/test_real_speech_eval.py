"""Fail fast before optional dataset/model downloads in manual ASR evaluation."""
import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

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
