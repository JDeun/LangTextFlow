"""CPU-local M2M100 adapter contracts without downloading a checkpoint."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from langtextflow.config import Settings
from langtextflow.local_mt_preflight import local_m2m100_check
from langtextflow.models import SessionContext, StartSessionRequest
from langtextflow.pipeline import CaptionPipeline
from langtextflow.translation import M2M100CT2Translator, TranslationError


class _FakeTokenizer:
    lang_code_to_token = {"ko": "__ko__", "en": "__en__", "ja": "__ja__"}

    def __init__(self) -> None:
        self.src_lang = "ko"
        self.seen: list[str] = []

    def encode(self, text: str) -> list[int]:
        self.seen.append(self.src_lang)
        return [42]

    def convert_ids_to_tokens(self, ids: list[int]) -> list[str]:
        assert ids == [42]
        return ["source-piece"]

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        assert tokens == ["translated-piece"]
        return [11]

    def decode(self, ids: list[int], *, skip_special_tokens: bool) -> str:
        assert ids == [11] and skip_special_tokens
        return "Verified translation text"


class _FakeEngine:
    def __init__(self, *, wrong_prefix: bool = False) -> None:
        self.wrong_prefix = wrong_prefix
        self.targets: list[str] = []

    def translate_batch(
        self, tokens: list[list[str]], *, target_prefix: list[list[str]],
        beam_size: int, max_decoding_length: int,
    ) -> list[SimpleNamespace]:
        assert tokens == [["source-piece"]]
        assert beam_size == 1 and max_decoding_length == 256
        code = target_prefix[0][0]
        self.targets.append(code)
        if self.wrong_prefix:
            code = "__unexpected__"
        return [SimpleNamespace(hypotheses=[[code, "translated-piece"]])]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("ko", "en"), ("ko", "ja"), ("en", "ko"),
        ("en", "ja"), ("ja", "ko"), ("ja", "en"),
    ],
)
async def test_runtime_m2m100_routes_six_supported_directions(
    source: str, target: str,
) -> None:
    provider = M2M100CT2Translator(model_dir=".", tokenizer_dir=".")
    fake_tokenizer = _FakeTokenizer()
    fake_engine = _FakeEngine()
    provider._tokenizer = fake_tokenizer
    provider._engine = fake_engine
    output = await provider.translate(
        "Source speech", source_language=source, target_language=target,
        context=SessionContext(),
    )
    assert output == "Verified translation text"
    assert fake_tokenizer.seen == [source]
    assert fake_engine.targets == [f"__{target}__"]


@pytest.mark.asyncio
async def test_local_m2m100_fails_closed_if_model_not_prepared(tmp_path) -> None:
    provider = M2M100CT2Translator(
        model_dir=str(tmp_path / "absent-ct2"),
        tokenizer_dir=str(tmp_path / "absent-tokenizer"),
    )
    with pytest.raises(TranslationError, match="cache is missing"):
        await provider.prepare()
    with pytest.raises(TranslationError, match="not prepared"):
        await provider.translate(
            "안녕", source_language="ko", target_language="en",
            context=SessionContext(),
        )


@pytest.mark.asyncio
async def test_local_m2m100_rejects_unsupported_and_same_language() -> None:
    provider = M2M100CT2Translator(model_dir=".", tokenizer_dir=".")
    for source, target in [("fr", "en"), ("ko", "ko"), ("en", "fr")]:
        with pytest.raises(TranslationError, match="distinct ko/en/ja"):
            await provider.translate(
                "Speech", source_language=source, target_language=target,
                context=SessionContext(),
            )


@pytest.mark.asyncio
async def test_local_m2m100_rejects_incorrect_target_prefix() -> None:
    provider = M2M100CT2Translator(model_dir=".", tokenizer_dir=".")
    provider._tokenizer = _FakeTokenizer()
    provider._engine = _FakeEngine(wrong_prefix=True)
    with pytest.raises(TranslationError, match="target language prefix"):
        await provider.translate(
            "Speech", source_language="en", target_language="ko",
            context=SessionContext(),
        )


def test_runtime_selects_local_m2m100_without_network() -> None:
    settings = Settings(
        local_m2m100_ct2_dir="/tmp/ltf-test-ct2-cache",
        local_m2m100_tokenizer_dir="/tmp/ltf-test-tokenizer-cache",
    )
    pipeline = CaptionPipeline(lambda event: None, settings)
    provider = pipeline._build_translator(
        StartSessionRequest(
            source_language="en", target_languages=["ko", "ja"],
            translation_provider="m2m100-ct2",
        )
    )
    assert isinstance(provider, M2M100CT2Translator)
    assert str(provider.model_dir) == settings.local_m2m100_ct2_dir


def test_local_m2m100_preflight_blocks_missing_artifacts(tmp_path) -> None:
    settings = Settings(
        local_m2m100_ct2_dir=str(tmp_path / "not-provisioned"),
        local_m2m100_tokenizer_dir=str(tmp_path / "also-missing"),
    )
    result = local_m2m100_check(settings)
    assert result.id == "m2m100-local"
    assert result.status == "missing"
    assert "model.bin" in result.summary


@pytest.mark.parametrize("threads", [0, 17])
def test_local_m2m100_refuses_unbounded_threads(threads: int) -> None:
    with pytest.raises(ValueError, match="thread count"):
        M2M100CT2Translator(model_dir=".", tokenizer_dir=".", cpu_threads=threads)
