"""CPU ASR candidate contracts: no synthetic result may be marked measured."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.benchmark_cpu_asr_candidates import (
    CANDIDATES,
    model_requirements,
    validate_model_files,
)


def test_every_candidate_requires_actual_model_weights(tmp_path: Path) -> None:
    assert len(CANDIDATES) == 5
    for name in CANDIDATES:
        with pytest.raises(FileNotFoundError, match="missing files"):
            validate_model_files(name, tmp_path)


@pytest.mark.parametrize(
    ("candidate", "required"),
    [
        ("sensevoice", "model.int8.onnx"),
        ("qwen3-0.6b", "encoder.int8.onnx"),
        ("qwen3-1.7b", "decoder.int8.onnx"),
        ("zipformer-ko", "joiner-epoch-99-avg-1.int8.onnx"),
        ("whispercpp-base", "ggml-base.bin"),
    ],
)
def test_candidate_has_correct_real_model_file(
    candidate: str, required: str,
) -> None:
    assert required in model_requirements(candidate)


def test_all_required_model_paths_must_exist(tmp_path: Path) -> None:
    model = tmp_path / "sensevoice"
    model.mkdir()
    (model / "model.int8.onnx").write_bytes(b"placeholder")
    with pytest.raises(FileNotFoundError, match="tokens.txt"):
        validate_model_files("sensevoice", model)
    (model / "tokens.txt").write_text("hello")
    validate_model_files("sensevoice", model)
