"""Offline local MT readiness rejects partial caches before provider selection."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from langtextflow.local_mt_preflight import local_m2m100_check
from langtextflow.preflight import CheckStatus


def test_m2m100_partial_tokenizer_cache_is_not_ready(tmp_path) -> None:
    model = tmp_path / "model"
    tokenizer = tmp_path / "tokenizer"
    model.mkdir()
    tokenizer.mkdir()
    for name in ("model.bin", "config.json"):
        (model / name).write_bytes(b"fixture")
    (tokenizer / "tokenizer_config.json").write_text("{}")
    settings = SimpleNamespace(
        local_m2m100_ct2_dir=str(model),
        local_m2m100_tokenizer_dir=str(tokenizer),
    )
    with patch("langtextflow.local_mt_preflight.importlib.util.find_spec",
               return_value=object()):
        result = local_m2m100_check(settings)
    assert result.status == CheckStatus.MISSING
    assert "SentencePiece" in result.summary

    (tokenizer / "sentencepiece.bpe.model").write_bytes(b"fixture")
    with patch("langtextflow.local_mt_preflight.importlib.util.find_spec",
               return_value=object()):
        result = local_m2m100_check(settings)
    assert result.status == CheckStatus.READY
