"""Real first-party OS-child recovery, with synthetic native libraries only."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from langtextflow.models import SessionContext
from langtextflow.translation import m2m100_ct2 as mt
from langtextflow.translation.base import TranslationError


@pytest.fixture
def recovering_provider(tmp_path, monkeypatch):
    for name in ("model.bin", "config.json", "tokenizer_config.json",
                 "sentencepiece.bpe.model", "vocab.json"):
        (tmp_path / name).touch()
    (tmp_path / "transformers.py").write_text('''
class M2M100Tokenizer:
    lang_code_to_token = {lang: "__" + lang + "__" for lang in ("ko", "en", "ja")}
    @classmethod
    def from_pretrained(cls, path, *, local_files_only):
        assert local_files_only
        return cls()
    def encode(self, text): return [text]
    def convert_ids_to_tokens(self, values): return values
    def convert_tokens_to_ids(self, values): return values
    def decode(self, values, **kwargs): return " ".join(values)
''', encoding="utf-8")
    (tmp_path / "ctranslate2.py").write_text('''
import json, os, time
from pathlib import Path
from types import SimpleNamespace
class Translator:
    def __init__(self, path, **kwargs):
        self.path = Path(path)
        self.log("load")
        if (self.path / "load-error").exists(): raise ValueError("native load failure")
        if (self.path / "load-hang").exists(): time.sleep(60)
    def log(self, stage, **values):
        with (self.path / "calls.jsonl").open("a") as out:
            out.write(json.dumps({"stage":stage,"pid":os.getpid(),**values}) + "\\n")
    def translate_batch(self, inputs, *, target_prefix, beam_size, max_decoding_length):
        text = inputs[0][0]
        target = target_prefix[0][0]
        self.log("infer", text=text, target=target)
        if text == "CRASH": os._exit(7)
        if text == "HANG": time.sleep(60)
        output = text if text == "2010..." else "verified-" + target[2:-2]
        return [SimpleNamespace(hypotheses=[[target, output]])]
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([
        str(tmp_path), str(Path(__file__).resolve().parents[1]),
    ]))
    monkeypatch.setenv("USE_TORCH", "0")
    monkeypatch.setattr(mt, "LOAD_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(mt, "INFERENCE_TIMEOUT_SECONDS", .2)
    monkeypatch.setattr(mt, "TERMINATE_GRACE_SECONDS", .05)
    monkeypatch.setattr(mt, "REAP_TIMEOUT_SECONDS", 2)
    return mt.M2M100CT2Translator(model_dir=str(tmp_path), tokenizer_dir=str(tmp_path)), tmp_path


def records(path):
    return [json.loads(line) for line in (path / "calls.jsonl").read_text().splitlines()]


async def wait_load(path, count):
    async with asyncio.timeout(5):
        while sum(row["stage"] == "load" for row in records(path)) < count:
            await asyncio.sleep(.005)


async def translate(provider, text="VALID", target="en"):
    return await provider.translate(text, source_language="ko", target_language=target,
                                    context=SessionContext())


def reaped(worker):
    assert worker._released and worker.process.returncode is not None
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(worker.process.pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["2010...", "CRASH", "HANG"])
async def test_next_caption_recovers_terminal_native_owner_once_for_concurrent_targets(
    recovering_provider, failure,
):
    provider, path = recovering_provider
    await provider.prepare()
    original = provider._worker
    try:
        with pytest.raises(TranslationError):
            await translate(provider, failure)
        reaped(original)
        assert await asyncio.gather(translate(provider), translate(provider, target="ja")) == [
            "verified-en", "verified-ja",
        ]
        replacement = provider._worker
        assert replacement is not original
        assert sum(row["stage"] == "load" for row in records(path)) == 2
        assert sum(row.get("text") == failure for row in records(path)) == 1
    finally:
        await provider.close()
    reaped(replacement)


@pytest.mark.asyncio
async def test_stop_during_recovery_load_fences_active_and_waiting_targets(recovering_provider):
    provider, path = recovering_provider
    await provider.prepare()
    with pytest.raises(TranslationError):
        await translate(provider, "2010...")
    (path / "load-hang").touch()
    active = asyncio.create_task(translate(provider))
    await wait_load(path, 2)
    worker = provider._worker
    queued = asyncio.create_task(translate(provider, target="ja"))
    await asyncio.sleep(.01)
    try:
        await provider.close()
        results = await asyncio.wait_for(asyncio.gather(active, queued, return_exceptions=True), 3)
        assert all(isinstance(result, TranslationError) for result in results)
        reaped(worker)
        assert provider._worker is None
        with pytest.raises(TranslationError, match="not prepared"):
            await translate(provider)
        assert sum(row["stage"] == "load" for row in records(path)) == 2
    finally:
        await provider.close()
        await asyncio.gather(active, queued, return_exceptions=True)


@pytest.mark.asyncio
async def test_repeated_cancel_during_recovery_reaps_and_cannot_revive_after_stop(
    recovering_provider,
):
    provider, path = recovering_provider
    await provider.prepare()
    with pytest.raises(TranslationError):
        await translate(provider, "2010...")
    (path / "load-hang").touch()
    pending = asyncio.create_task(translate(provider))
    await wait_load(path, 2)
    worker = provider._worker
    try:
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 3)
        reaped(worker)
        await provider.close()
        with pytest.raises(TranslationError, match="not prepared"):
            await translate(provider)
        assert provider._worker is None
        assert sum(row["stage"] == "load" for row in records(path)) == 2
    finally:
        await provider.close()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_while_recovery_waits_for_old_reap_prevents_new_spawn(
    recovering_provider, monkeypatch,
):
    provider, path = recovering_provider
    await provider.prepare()
    original = provider._worker
    with pytest.raises(TranslationError):
        await translate(provider, "2010...")
    entered, release = asyncio.Event(), asyncio.Event()
    close = original.close

    async def deferred_close():
        entered.set()
        await release.wait()
        await close()

    monkeypatch.setattr(original, "close", deferred_close)
    recovering = asyncio.create_task(translate(provider))
    await entered.wait()
    stopping = asyncio.create_task(provider.close())
    await asyncio.sleep(0)
    release.set()
    try:
        await asyncio.wait_for(stopping, 3)
        with pytest.raises(TranslationError):
            await asyncio.wait_for(recovering, 3)
        assert provider._worker is None
        assert sum(row["stage"] == "load" for row in records(path)) == 1
        reaped(original)
    finally:
        release.set()
        await asyncio.gather(recovering, stopping, return_exceptions=True)
        await provider.close()


@pytest.mark.asyncio
async def test_concurrent_targets_share_one_failed_recovery_attempt_then_later_call_can_retry(
    recovering_provider,
):
    provider, path = recovering_provider
    await provider.prepare()
    with pytest.raises(TranslationError):
        await translate(provider, "2010...")
    (path / "load-error").touch()
    try:
        for wave in range(2):
            # Both calls capture the unavailable owner before the next lock
            # handoff, including the None→failed-load→None ABA transition.
            async with provider._prepare_lock:
                pending = [asyncio.create_task(translate(provider)),
                           asyncio.create_task(translate(provider, target="ja"))]
                await asyncio.sleep(0)
            results = await asyncio.gather(*pending, return_exceptions=True)
            assert all(isinstance(result, TranslationError) for result in results)
            assert sum(row["stage"] == "load" for row in records(path)) == 2 + wave
        (path / "load-error").unlink()
        assert await translate(provider, "NEXT") == "verified-en"
        assert sum(row["stage"] == "load" for row in records(path)) == 4
    finally:
        await provider.close()
