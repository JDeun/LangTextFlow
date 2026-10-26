"""M2M100 generated captions respect operator-scoped glossary or fail closed."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from langtextflow.models import GlossaryEntry, SessionContext
from langtextflow.translation.base import TranslationError
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator


@pytest.mark.asyncio
async def test_local_mt_rejects_missing_required_glossary_translation(tmp_path) -> None:
    provider = M2M100CT2Translator(
        model_dir=str(tmp_path), tokenizer_dir=str(tmp_path),
    )
    provider._engine = object()
    provider._tokenizer = object()
    context = SessionContext(glossary=[
        GlossaryEntry(term="성령", source_language="ko", translations={"en": "Holy Spirit"}),
    ])
    with (
        patch.object(provider, "_translate_sync", return_value="The spirit came."),
        pytest.raises(TranslationError, match="glossary term not preserved"),
    ):
            await provider.translate(
                "성령이 오셨습니다", source_language="ko",
                target_language="en", context=context,
            )


@pytest.mark.asyncio
async def test_local_mt_accepts_matching_glossary_translation(tmp_path) -> None:
    provider = M2M100CT2Translator(
        model_dir=str(tmp_path), tokenizer_dir=str(tmp_path),
    )
    provider._engine = object()
    provider._tokenizer = object()
    context = SessionContext(glossary=[
        GlossaryEntry(term="성령", source_language="ko", translations={"en": "Holy Spirit"}),
    ])
    with patch.object(provider, "_translate_sync", return_value="The Holy Spirit came."):
        result = await provider.translate(
            "성령이 오셨습니다", source_language="ko",
            target_language="en", context=context,
        )
    assert result == "The Holy Spirit came."
