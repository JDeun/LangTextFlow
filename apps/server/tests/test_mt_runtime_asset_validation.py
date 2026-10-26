"""Runtime M2M100 prepare must reject partial caches without imports or network."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from langtextflow.translation.base import TranslationError
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator


@pytest.mark.asyncio
async def test_m2m100_prepare_rejects_partial_tokenizer_without_native_import(tmp_path) -> None:
    model = tmp_path / "model"
    tokenizer = tmp_path / "tokenizer"
    model.mkdir()
    tokenizer.mkdir()
    for filename in ("model.bin", "config.json"):
        (model / filename).write_bytes(b"fixture")
    (tokenizer / "tokenizer_config.json").write_text("{}")
    provider = M2M100CT2Translator(
        model_dir=str(model), tokenizer_dir=str(tokenizer),
    )
    with patch.object(provider, "_prepare_sync") as native_prepare:
        with pytest.raises(TranslationError, match="missing"):
            await provider.prepare()
    native_prepare.assert_not_called()
