"""CPU-local M2M100 adapter contracts without downloading a checkpoint."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from langtextflow.config import Settings
from langtextflow.local_mt_preflight import local_m2m100_check
from langtextflow.models import SessionContext, StartSessionRequest
from langtextflow.pipeline import CaptionPipeline
from langtextflow.runtime import CaptionRuntime
from langtextflow.translation import M2M100CT2Translator, TranslationError
from langtextflow.translation.capabilities import translation_capabilities


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
    output = provider._translate_sync("Source speech", source, target, fake_tokenizer, fake_engine)
    assert output == "Verified translation text"
    assert fake_tokenizer.seen == [source]
    assert fake_engine.targets == [f"__{target}__"]


@pytest.mark.parametrize("text", ["17", " 17 ", "017"])
def test_unchanged_decimal_caption_preserves_language_independent_digits(monkeypatch, text):
    tokenizer = _FakeTokenizer()
    monkeypatch.setattr(tokenizer, "decode", lambda *args, **kwargs: text.strip())
    provider = M2M100CT2Translator(model_dir=".", tokenizer_dir=".")
    assert provider._translate_sync(text, "en", "ko", tokenizer, _FakeEngine()) == text.strip()


@pytest.mark.parametrize("text", ["Speech", "2010...", "17위", "1.7", "[17]", "", "０１７"])
def test_nondecimal_source_copy_remains_rejected(monkeypatch, text):
    tokenizer = _FakeTokenizer()
    monkeypatch.setattr(tokenizer, "decode", lambda *args, **kwargs: text)
    provider = M2M100CT2Translator(model_dir=".", tokenizer_dir=".")
    with pytest.raises(TranslationError):
        provider._translate_sync(text, "en", "ko", tokenizer, _FakeEngine())


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
@pytest.mark.parametrize("text", ["Speech", "17"])
async def test_local_m2m100_rejects_incorrect_target_prefix(text) -> None:
    provider = M2M100CT2Translator(model_dir=".", tokenizer_dir=".")
    with pytest.raises(TranslationError, match="target language prefix"):
        provider._translate_sync(text, "en", "ko", _FakeTokenizer(),
                                 _FakeEngine(wrong_prefix=True))


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


@pytest.fixture
def local_model_files(tmp_path):
    model = tmp_path / "ct2"
    tokenizer = tmp_path / "tokenizer"
    model.mkdir()
    tokenizer.mkdir()
    for name in ("model.bin", "config.json"):
        (model / name).write_text("fixture")
    for name in ("tokenizer_config.json", "sentencepiece.bpe.model", "vocab.json"):
        (tokenizer / name).write_text("fixture")
    return model, tokenizer


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [
    "model.bin", "config.json", "tokenizer_config.json", "sentencepiece.bpe.model", "vocab.json",
])
async def test_preflight_and_prepare_reject_each_missing_file(
    local_model_files, monkeypatch, missing,
) -> None:
    model, tokenizer = local_model_files
    directory = model if missing in {"model.bin", "config.json"} else tokenizer
    (directory / missing).unlink()
    monkeypatch.setattr(
        "langtextflow.local_mt_preflight.importlib.util.find_spec", lambda _: object(),
    )
    check = local_m2m100_check(Settings(
        local_m2m100_ct2_dir=str(model), local_m2m100_tokenizer_dir=str(tokenizer),
    ))
    assert check.status == "missing" and missing in check.summary
    provider = M2M100CT2Translator(model_dir=str(model), tokenizer_dir=str(tokenizer))
    monkeypatch.setattr(
        provider, "_prepare_sync", lambda: pytest.fail("incomplete cache reached native loader"),
    )
    with pytest.raises(TranslationError, match="cache is missing"):
        await provider.prepare()


def test_complete_local_cache_remains_ready(local_model_files, monkeypatch) -> None:
    model, tokenizer = local_model_files
    monkeypatch.setattr(
        "langtextflow.local_mt_preflight.importlib.util.find_spec", lambda _: object(),
    )
    assert local_m2m100_check(Settings(
        local_m2m100_ct2_dir=str(model), local_m2m100_tokenizer_dir=str(tokenizer),
    )).status == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TypeError, KeyError])
async def test_malformed_tokenizer_initialization_preserves_live_source(
    local_model_files, monkeypatch, tmp_path, failure,
) -> None:
    model, tokenizer = local_model_files

    async def broken_tokenizer(self):
        raise failure("malformed tokenizer fixture")

    monkeypatch.setattr(
        "langtextflow.translation.m2m100_ct2._M2M100Worker.start", broken_tokenizer,
    )
    runtime = CaptionRuntime(Settings(
        database_path=str(tmp_path / "history.db"),
        local_m2m100_ct2_dir=str(model), local_m2m100_tokenizer_dir=str(tokenizer),
    ))
    try:
        state = await runtime.start(StartSessionRequest(
            engine="mock", correction_provider="none", translation_provider="m2m100-ct2",
            source_language="en", target_languages=["ko"],
        ))
        assert state.running and not state.translation_status.available
        assert "could not initialize" in state.translation_status.error
        for _ in range(40):
            if any(event.committed for event in runtime.store.snapshot()):
                break
            await asyncio.sleep(0.05)
        sources = [event for event in runtime.store.snapshot() if event.committed]
        assert sources and all(event.source_language == "en" for event in sources)
        assert all(not event.translations for event in sources)
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize("threads", [0, 17])
def test_local_m2m100_refuses_unbounded_threads(threads: int) -> None:
    with pytest.raises(ValueError, match="thread count"):
        M2M100CT2Translator(model_dir=".", tokenizer_dir=".", cpu_threads=threads)


def test_provider_capabilities_do_not_mislabel_remote_proxy_as_local() -> None:
    remote = Settings(
        ollama_url="https://ollama.remote.example/api",
        openai_compatible_url="https://api.example.invalid/v1",
    )
    descriptions = {
        item.provider: item for item in translation_capabilities(remote)
    }
    assert descriptions["ollama"].local_only is False
    assert descriptions["openai-compatible"].local_only is False
    assert descriptions["ollama"].supported_pairs is None
    assert descriptions["m2m100-ct2"].supported_pairs is not None
    assert descriptions["m2m100-ct2"].field_qualified is False

    local = Settings(
        ollama_url="http://127.0.0.1:11434",
        openai_compatible_url="http://localhost:1234/v1",
    )
    descriptions = {
        item.provider: item for item in translation_capabilities(local)
    }
    assert descriptions["ollama"].local_only is True
    assert descriptions["openai-compatible"].local_only is True


@pytest.mark.parametrize(
    ("source", "targets"),
    [("fr", ["en"]), ("en", ["fr"]), ("ja", ["ko", "es"])],
)
def test_pipeline_fails_unsupported_local_mt_pairs_before_loading(
    source: str, targets: list[str],
) -> None:
    pipeline = CaptionPipeline(lambda event: None, Settings())
    with pytest.raises(ValueError, match="six directed ko/en/ja pairs"):
        pipeline._build_translator(
            StartSessionRequest(
                source_language=source,
                target_languages=targets,
                translation_provider="m2m100-ct2",
            )
        )
