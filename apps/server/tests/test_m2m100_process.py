"""Real OS-child lifecycle tests, with synthetic IPC/native fixtures and no weights."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from langtextflow.models import SessionContext
from langtextflow.translation import m2m100_ct2 as mt
from langtextflow.translation.base import TranslationError


async def test_prepare_watchdog_allows_native_import_to_query_console(tmp_path):
    """The Windows CRT locks a descriptor during blocking read; libraries query fd0."""
    (tmp_path / "ctranslate2.py").write_text('''
class Translator:
    def __init__(self, *args, **kwargs): pass
''')
    (tmp_path / "transformers.py").write_text('''
import os, time
from types import SimpleNamespace
time.sleep(.1)
os.isatty(0)
os.fstat(0)
os.close(os.dup(0))
class M2M100Tokenizer:
    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        return SimpleNamespace(lang_code_to_token={'ko':'ko','en':'en','ja':'ja'})
''')
    shim = tmp_path / "prepare.py"
    shim.write_text('''
import hashlib, sys
from dataclasses import replace
from langtextflow import m2m100_prepare_worker as worker
worker.FILES = tuple(replace(item, sha256=hashlib.sha256(b'fixture').hexdigest(),
                             maximum_bytes=32) for item in worker.FILES)
class Response:
    is_redirect = False
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_raw(self, size): yield b'fixture'
class Client:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def stream(self, *args, **kwargs): return Response()
worker.httpx.Client = Client
worker.main(sys.argv[1:])
''')
    stage = tmp_path / "stage"
    stage.mkdir()
    child = await asyncio.create_subprocess_exec(
        sys.executable, str(shim), str(stage), "0", str(tmp_path / "absent-model"),
        str(tmp_path / "absent-tokenizer"), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
    )
    try:
        # Keep stdin open. Closing it via communicate() would conceal the CRT lock conflict.
        line = await asyncio.wait_for(child.stdout.readline(), 4)
        assert json.loads(line) == {"ready": True}
        child.stdin.close()
        await asyncio.wait_for(child.wait(), 3)
        assert child.returncode == 0 and await child.stderr.read() == b""
    finally:
        child.stdin.close()
        try:
            await asyncio.wait_for(child.wait(), 3)
        except TimeoutError:
            child.kill()
            await child.wait()


@pytest.fixture
def process_provider(tmp_path, monkeypatch):
    for name in ("model.bin", "config.json", "tokenizer_config.json",
                 "sentencepiece.bpe.model", "vocab.json"):
        (tmp_path / name).touch()
    child = tmp_path / "child.py"
    child.write_text('''
import json, os, signal, sys, time
mode = sys.argv[1]
if mode in {"load-kill", "infer-kill"} and hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print(json.dumps({"pid": os.getpid()}), file=open(sys.argv[2], "w"), flush=True)
if mode == "load-crash":
    sys.exit(7)
if mode == "load-kill":
    time.sleep(60)
print('{"ready": true}', flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request["text"] == "HANG":
        open(sys.argv[2] + ".entered", "w").close()
        time.sleep(60)
    if mode == "infer-crash":
        sys.exit(8)
    if mode == "oversized":
        print("x" * 200000, flush=True)
        continue
    if mode == "wrong-id":
        request["id"] += 1
    print(json.dumps({"id": request["id"], "text": "verified-" + request["target"]}), flush=True)
''', encoding="utf-8")
    mode = ["normal"]
    pid_path = tmp_path / "pid.json"
    monkeypatch.setattr(mt, "worker_command", lambda *args: [
        sys.executable, "-u", str(child), mode[0], str(pid_path),
    ])
    monkeypatch.setattr(mt, "TERMINATE_GRACE_SECONDS", .08)
    monkeypatch.setattr(mt, "REAP_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(mt, "LOAD_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(mt, "INFERENCE_TIMEOUT_SECONDS", 1)

    def provider():
        return mt.M2M100CT2Translator(model_dir=str(tmp_path), tokenizer_dir=str(tmp_path))

    return provider, mode, pid_path


async def wait_file(path: Path):
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(.005)


async def translate(provider, text="source"):
    return await provider.translate(text, source_language="en", target_language="ko",
                                    context=SessionContext())


def assert_reaped(worker):
    assert worker.process.returncode is not None
    assert worker._released
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(worker.process.pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["load", "infer"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_native_hang_timeout_or_cancel_kills_reaps_and_can_retry(
    process_provider, boundary, cancel,
):
    factory, mode, pid_path = process_provider
    mode[0] = f"{boundary}-kill"
    provider = factory()
    if boundary == "load":
        pending = asyncio.create_task(provider.prepare())
        await wait_file(pid_path)
        worker = provider._worker
    else:
        await provider.prepare()
        worker = provider._worker
        pending = asyncio.create_task(translate(provider, "HANG"))
        await wait_file(Path(str(pid_path) + ".entered"))
    started = time.monotonic()
    if cancel:
        pending.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TranslationError):
        await asyncio.wait_for(pending, 4)
    assert time.monotonic() - started < 3
    assert_reaped(worker)
    if os.name == "posix":
        assert worker.process.returncode == -signal.SIGKILL
    await provider.close()
    # No process or job slot from the failed owner prevents a fresh provider.
    mode[0] = "normal"
    replacement = factory()
    try:
        await replacement.prepare()
        assert await translate(replacement) == "verified-ko"
    finally:
        await replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode_name", ["load-crash", "infer-crash", "wrong-id", "oversized"])
async def test_child_crash_or_invalid_output_fails_closed_and_reaps(process_provider, mode_name):
    factory, mode, _ = process_provider
    mode[0] = mode_name
    provider = factory()
    worker = None
    try:
        if mode_name == "load-crash":
            with pytest.raises(TranslationError, match="could not initialize"):
                await provider.prepare()
        else:
            await provider.prepare()
            worker = provider._worker
            with pytest.raises(TranslationError):
                await translate(provider)
            assert_reaped(worker)
    finally:
        await provider.close()
    mode[0] = "normal"
    await provider.prepare()
    try:
        assert await translate(provider) == "verified-ko"
        assert await translate(provider, "😀" * 20000) == "verified-ko"
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_stop_reaps_active_child_invalidates_queued_results_and_releases_capacity(
    process_provider,
):
    factory, _, pid_path = process_provider
    provider = factory()
    await provider.prepare()
    worker = provider._worker
    active = asyncio.create_task(translate(provider, "HANG"))
    await wait_file(Path(str(pid_path) + ".entered"))
    queued = [asyncio.create_task(translate(provider)) for _ in range(3)]
    await asyncio.sleep(.01)
    with pytest.raises(TranslationError, match="at capacity"):
        await translate(provider)
    other = factory()
    with pytest.raises(TranslationError, match="at capacity"):
        await other.prepare()
    await provider.close()
    results = await asyncio.wait_for(asyncio.gather(active, *queued, return_exceptions=True), 2)
    assert all(isinstance(result, TranslationError) for result in results)
    assert_reaped(worker)
    for _ in range(3):
        await provider.prepare()
        assert await translate(provider) == "verified-ko"
        fresh = provider._worker
        await provider.close()
        assert_reaped(fresh)


@pytest.mark.asyncio
async def test_cancel_queued_request_does_not_kill_active_owner(process_provider):
    factory, _, pid_path = process_provider
    provider = factory()
    await provider.prepare()
    active = asyncio.create_task(translate(provider, "HANG"))
    await wait_file(Path(str(pid_path) + ".entered"))
    queued = asyncio.create_task(translate(provider))
    await asyncio.sleep(.01)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert provider._worker.ready and provider._worker.process.returncode is None
    await provider.close()
    await asyncio.gather(active, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_os_launch_reaps_late_created_child(process_provider, monkeypatch):
    factory, _, _ = process_provider
    create = asyncio.create_subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()

    async def deferred(*args, **kwargs):
        entered.set()
        await release.wait()
        return await create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", deferred)
    provider = factory()
    pending = asyncio.create_task(provider.prepare())
    await entered.wait()
    worker = provider._worker
    pending.cancel()
    await asyncio.sleep(.01)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 3)
    assert_reaped(worker)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["idle", "load", "infer"])
async def test_first_party_child_uses_native_contract_offline_and_parent_eof_exits(
    tmp_path, monkeypatch, phase,
):
    # Fake only the native modules; exercise the actual shipped child entrypoint.
    (tmp_path / "transformers.py").write_text('''
import os
import time
from pathlib import Path
class M2M100Tokenizer:
    lang_code_to_token = {"ko": "__ko__", "en": "__en__", "ja": "__ja__"}
    @classmethod
    def from_pretrained(cls, path, *, local_files_only):
        assert local_files_only and os.environ["HF_HUB_OFFLINE"] == "1"
        if (Path(path) / "hang-load").exists():
            (Path(path) / "entered").touch()
            time.sleep(60)
        return cls()
    def encode(self, text): return [42]
    def convert_ids_to_tokens(self, ids): return ["source"]
    def convert_tokens_to_ids(self, tokens): return [11]
    def decode(self, ids, *, skip_special_tokens): return "Verified translation text"
''', encoding="utf-8")
    (tmp_path / "ctranslate2.py").write_text('''
from types import SimpleNamespace
from pathlib import Path
import time
class Translator:
    def __init__(self, path, *, device, compute_type, inter_threads, intra_threads):
        assert (device, compute_type, inter_threads, intra_threads) == ("cpu", "int8", 1, 3)
        self.path = Path(path)
        print("native load logging must not corrupt private stdout")
    def translate_batch(self, tokens, *, target_prefix, beam_size, max_decoding_length):
        assert tokens == [["source"]] and beam_size == 1 and max_decoding_length == 256
        if (self.path / "hang-infer").exists():
            (self.path / "entered").touch()
            time.sleep(60)
        return [SimpleNamespace(hypotheses=[[target_prefix[0][0], "translated"]])]
''', encoding="utf-8")
    for name in ("model.bin", "config.json", "tokenizer_config.json",
                 "sentencepiece.bpe.model", "vocab.json"):
        (tmp_path / name).touch()
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([
        str(tmp_path), str(Path(__file__).parents[1]), os.environ.get("PYTHONPATH", ""),
    ]))
    provider = mt.M2M100CT2Translator(
        model_dir=str(tmp_path), tokenizer_dir=str(tmp_path), cpu_threads=3,
    )
    pending = None
    try:
        if phase == "load":
            (tmp_path / "hang-load").touch()
            pending = asyncio.create_task(provider.prepare())
            await wait_file(tmp_path / "entered")
            worker = provider._worker
        else:
            await provider.prepare()
            worker = provider._worker
            if phase == "infer":
                (tmp_path / "hang-infer").touch()
                pending = asyncio.create_task(translate(provider))
                await wait_file(tmp_path / "entered")
            else:
                for source in mt.SUPPORTED_LANGUAGES:
                    for target in mt.SUPPORTED_LANGUAGES - {source}:
                        assert await provider.translate(
                            "source speech", source_language=source, target_language=target,
                            context=SessionContext(),
                        ) == "Verified translation text"
        # Parent death closes stdin without a graceful stop/TERM request.
        worker.process.stdin.close()
        assert await asyncio.wait_for(worker.process.wait(), 3) != 0
        if pending:
            with pytest.raises(TranslationError):
                await asyncio.wait_for(pending, 3)
    finally:
        await provider.close()
        if pending:
            await asyncio.gather(pending, return_exceptions=True)
    assert_reaped(worker)


def test_worker_command_source_and_frozen(monkeypatch, tmp_path):
    assert mt.worker_command(tmp_path, tmp_path, 2)[1:3] == [
        "-m", "langtextflow.translation.m2m100_worker",
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert mt.worker_command(tmp_path, tmp_path, 2)[1] == "--m2m100-worker"


@pytest.mark.asyncio
async def test_launch_callback_before_cleanup_timeout_completion_still_reaps(
    process_provider, monkeypatch,
):
    factory, _, _ = process_provider
    create = asyncio.create_subprocess_exec
    wait_for = asyncio.wait_for
    spawned, release = asyncio.Event(), asyncio.Event()
    timed_out = asyncio.Event()
    processes = []

    async def delayed_return(*args, **kwargs):
        process = await create(*args, **kwargs)
        processes.append(process)
        spawned.set()
        await release.wait()
        return process

    async def complete_launch_before_propagating_timeout(awaitable, timeout):
        try:
            return await wait_for(awaitable, timeout)
        except TimeoutError:
            if not timed_out.is_set():
                timed_out.set()
                # Use a real timeout, then force launch's callback to run while
                # the closing task is still propagating that timeout (3.11 and 3.12).
                release.set()
                await worker._launch
                await asyncio.sleep(0)
            raise

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_return)
    monkeypatch.setattr(asyncio, "wait_for", complete_launch_before_propagating_timeout)
    monkeypatch.setattr(mt, "REAP_TIMEOUT_SECONDS", .03)
    provider = factory()
    worker = mt._M2M100Worker(mt.worker_command(provider.model_dir, provider.tokenizer_dir, 2))
    await spawned.wait()
    stopping = asyncio.create_task(worker.close())
    try:
        with pytest.raises(TranslationError, match="launch cleanup timed out"):
            await stopping
        assert timed_out.is_set()
        async with asyncio.timeout(5):
            while not worker._released:
                await asyncio.sleep(.005)
        assert worker._late_cleanup is not None
        assert processes[0].returncode is not None
        assert_reaped(worker)
    finally:
        release.set()
        monkeypatch.setattr(mt, "REAP_TIMEOUT_SECONDS", 2)
        await worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["load", "infer"])
async def test_first_party_native_error_exits_quietly_with_parent_stdin_open(
    tmp_path, monkeypatch, phase,
):
    (tmp_path / "transformers.py").write_text('''
class M2M100Tokenizer:
    lang_code_to_token = {"ko": "__ko__", "en": "__en__", "ja": "__ja__"}
    @classmethod
    def from_pretrained(cls, path, *, local_files_only):
        if path.endswith("load"): raise ValueError("corrupt local tokenizer")
        return cls()
    def encode(self, text): return [42]
    def convert_ids_to_tokens(self, ids): return ["source"]
''', encoding="utf-8")
    (tmp_path / "ctranslate2.py").write_text('''
class Translator:
    def __init__(self, *args, **kwargs): pass
    def translate_batch(self, *args, **kwargs): raise ValueError("native inference failed")
''', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([
        str(tmp_path), str(Path(__file__).parents[1]), os.environ.get("PYTHONPATH", ""),
    ]))
    process = await asyncio.create_subprocess_exec(
        *mt.worker_command(tmp_path, tmp_path / phase, 2),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        if phase == "infer":
            ready = await asyncio.wait_for(process.stdout.readline(), 5)
            assert json.loads(ready) == {"ready": True}
            process.stdin.write(b'{"id":1,"text":"source","source":"en","target":"ko"}\n')
            await process.stdin.drain()
        # Keep stdin open: a blocked daemon buffered reader must not abort Python finalization.
        assert await asyncio.wait_for(process.wait(), 5) == 1
        assert await process.stdout.read() == b""
        assert await process.stderr.read() == b""
    finally:
        process.stdin.close()
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), 3)
