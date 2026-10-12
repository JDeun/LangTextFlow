"""Fail fast before optional dataset/model downloads in manual ASR evaluation."""
import pytest

from scripts.evaluate_fleurs_asr import (
    CONFIGS,
    FLEURS_LICENSE,
    FLEURS_REVISION,
    evaluate,
)


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
