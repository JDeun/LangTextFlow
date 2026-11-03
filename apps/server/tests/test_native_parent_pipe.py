"""First-party legacy native worker/import boundary, with external model stubs."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized", [False, True], ids=["native-import-and-rpc", "bounded-frame"])
async def test_legacy_native_worker_import_can_clone_open_parent_stdin(tmp_path, oversized):
    fixture = tmp_path / "native_fixture.py"
    fixture.write_text('''
import os, sys, types
class Tokenizer:
    lang_code_to_token = {lang: "__"+lang+"__" for lang in ("ko","en","ja")}
    @classmethod
    def from_pretrained(cls, path, **kwargs):
        assert kwargs == {"local_files_only": True}
        return cls()
    def encode(self, text): return [text]
    def convert_ids_to_tokens(self, values): return values
    def convert_tokens_to_ids(self, values): return values
    def decode(self, values, **kwargs): return "translated"
class LazyTokenizer(types.ModuleType):
    def __getattr__(self, name):
        if name != "M2M100Tokenizer": raise AttributeError(name)
        os.close(os.dup(0))
        return Tokenizer
class Native:
    def __init__(self, *args, **kwargs): pass
    def translate_batch(self, inputs, **kwargs):
        return [types.SimpleNamespace(hypotheses=[[kwargs["target_prefix"][0][0], "target"]])]
sys.modules["transformers"] = LazyTokenizer("transformers")
sys.modules["ctranslate2"] = types.SimpleNamespace(Translator=Native)
from langtextflow.translation.m2m100_worker import main
main()
''', encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(fixture), str(tmp_path), str(tmp_path), "2",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    try:
        assert json.loads(await asyncio.wait_for(process.stdout.readline(), 3)) == {"ready": True}
        if oversized:
            process.stdin.write(b"x" * (128 * 1024))
            with suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.drain()
        else:
            process.stdin.write((json.dumps({"id": 1, "text": "source",
                                            "source": "ko", "target": "en"})+"\n").encode())
            await process.stdin.drain()
            assert json.loads(await asyncio.wait_for(process.stdout.readline(), 3)) == {
                "id": 1, "text": "translated",
            }
            process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 3) == 1
        assert await process.stderr.read() == b""
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
