"""Real preparation contracts with tiny byte fixtures, never model downloads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from langtextflow import m2m100_prepare_worker as prepare
from langtextflow.config import Settings
from langtextflow.local_mt_preflight import local_m2m100_check
from langtextflow.m2m100_cache import (
    m2m100_cache_paths,
    managed_m2m100_root,
    publish_m2m100_cache,
)
from langtextflow.model_setup import ModelSetupManager
from langtextflow.models import StartSessionRequest
from langtextflow.pipeline import CaptionPipeline
from langtextflow.translation import m2m100_ct2 as mt
from langtextflow.translation.capabilities import translation_capabilities


def fixture_file(data=b"verified"):
    return replace(prepare.FILES[0], sha256=hashlib.sha256(data).hexdigest(),
                   maximum_bytes=len(data))


class Blocks(httpx.SyncByteStream):
    def __init__(self, *blocks):
        self.blocks = blocks

    def __iter__(self):
        yield from self.blocks


def test_fixed_manifest_is_tied_to_independent_measured_checkpoint():
    assert prepare.MODEL_REVISION == "55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636"
    assert len({item.relative for item in prepare.FILES}) == 7
    assert prepare.FILES[0].sha256 == (
        "a1826980fc5c037e69c7ac94fcb56c03001a66f380eb71863cc0a3879e71421b")
    assert all(len(item.sha256) == 64 and len(item.revision) == 40 for item in prepare.FILES)
    assert prepare.DOWNLOAD_BYTES < 505 * 1024 * 1024


@pytest.mark.parametrize("url", [
    "http://huggingface.co/file", "https://huggingface.co.evil.test/file",
    "https://127.0.0.1/file", "https://user:secret@huggingface.co/file",
    "https://cas-bridge.xethub.hf.co:444/file", "file:///private/model.bin",
])
def test_download_redirect_destinations_are_allowlisted(url):
    assert not prepare._allowed_url(url)


@pytest.mark.parametrize("mode", ["hash", "size", "length", "encoding", "redirect", "loop"])
def test_download_rejects_untrusted_bytes_and_redirects(tmp_path, mode):
    def handler(request):
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://user:secret@evil.test/a"})
        if mode == "loop":
            return httpx.Response(302, headers={"location": str(request.url)})
        return httpx.Response(200,
                              headers=({"content-length": "999"} if mode == "length" else
                                       {"content-encoding": "gzip"} if mode == "encoding" else {}),
                              stream=Blocks(b"bad" if mode == "hash" else b"x" * 20))

    with (httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client,
          pytest.raises(ValueError)):
        prepare.download_file(client, fixture_file(), tmp_path / "file", 0)
    assert not (tmp_path / "file").exists() or (tmp_path / "file").stat().st_size <= 8


def test_download_follows_signed_hf_cdn_without_auth_and_hashes_actual_bytes(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={
                "location": "https://cas-bridge.xethub.hf.co/file?signature=private",
            })
        return httpx.Response(200, stream=Blocks(b"ver", b"ified"))

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        prepare.download_file(client, fixture_file(), tmp_path / "file", 0)
    assert (tmp_path / "file").read_bytes() == b"verified"
    assert len(seen) == 2 and all("authorization" not in request.headers for request in seen)


def test_pair_pointer_is_atomic_and_legacy_custom_paths_remain_explicit(tmp_path, monkeypatch):
    settings = Settings(model_cache_dir=str(tmp_path))
    root = managed_m2m100_root(settings)
    first, second = "a" * 32, "b" * 32
    for generation in (first, second):
        (root / generation / "model").mkdir(parents=True)
        (root / generation / "tokenizer").mkdir()
    publish_m2m100_cache(root, first)
    original_replace = prepare.os.replace
    monkeypatch.setattr("langtextflow.m2m100_cache.os.replace",
                        lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        publish_m2m100_cache(root, second)
    assert m2m100_cache_paths(settings) == (root / first / "model", root / first / "tokenizer")
    monkeypatch.setattr("langtextflow.m2m100_cache.os.replace", original_replace)
    publish_m2m100_cache(root, second)
    assert all(second in str(path) for path in m2m100_cache_paths(settings))
    custom = settings.model_copy(update={"local_m2m100_ct2_dir": "custom/model"})
    assert m2m100_cache_paths(custom)[0] == Path("custom/model")
    (root / "current.json").write_text('"../../private"')
    with pytest.raises(ValueError):
        m2m100_cache_paths(settings)


def test_managed_pair_reaches_real_capability_preflight_and_pipeline(tmp_path, monkeypatch):
    settings = Settings(model_cache_dir=str(tmp_path))
    root = managed_m2m100_root(settings)
    generation = "c" * 32
    for item in prepare.FILES:
        path = root / generation / item.relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    publish_m2m100_cache(root, generation)
    monkeypatch.setattr("langtextflow.local_mt_preflight.importlib.util.find_spec",
                        lambda _: object())
    assert local_m2m100_check(settings).status == "ready"
    assert next(item for item in translation_capabilities(settings)
                if item.provider == "m2m100-ct2").configured is True
    provider = CaptionPipeline(lambda event: None, settings)._build_translator(StartSessionRequest(
        source_language="ko", target_languages=["en"], translation_provider="m2m100-ct2",
    ))
    assert (provider.model_dir, provider.tokenizer_dir) == m2m100_cache_paths(settings)


async def test_active_native_worker_lease_blocks_cache_preparation(setup_manager):
    manager, _, processes = setup_manager
    assert mt._CHILD_SLOT.acquire(blocking=False)
    try:
        job = await manager.start_m2m100_prepare()
        await settle(manager)
        assert (await manager.get_job(job.job_id)).state == "error"
        assert not processes
        assert not (managed_m2m100_root(manager.settings) / "current.json").exists()
    finally:
        mt._CHILD_SLOT.release()


@pytest.fixture
def setup_manager(tmp_path, monkeypatch):
    # Replace only the spawned program; exercise real manager, worker lease and OS cleanup.
    monkeypatch.setattr("langtextflow.model_setup.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr(mt, "TERMINATE_GRACE_SECONDS", .05)
    monkeypatch.setattr(mt, "REAP_TIMEOUT_SECONDS", .1)
    manager = ModelSetupManager(Settings(model_cache_dir=str(tmp_path), storage_reserve_mb=64))
    original = asyncio.create_subprocess_exec
    mode = ["ready"]
    processes = []

    async def launch(*command, **kwargs):
        assert kwargs["env"]["USE_TORCH"] == "0"
        assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "0"
        stage = Path(command[-4])
        script = ("import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                  "(p/'model').mkdir(); (p/'tokenizer').mkdir(); "
                  + ("print('{\"ready\": true}',flush=True); sys.stdin.read()"
                     if mode[0] == "ready" else "time.sleep(60)"))
        process = await original(sys.executable, "-c", script, str(stage), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    return manager, mode, processes


async def settle(manager):
    async with asyncio.timeout(4):
        while manager.m2m100_preparing:
            await asyncio.sleep(.005)


async def test_manager_publishes_pair_reaps_child_and_cancel_before_start_releases(setup_manager):
    manager, _, processes = setup_manager
    first = await manager.start_m2m100_prepare()
    assert (await manager.start_m2m100_prepare()).job_id == first.job_id
    await settle(manager)
    assert (await manager.get_job(first.job_id)).state == "completed"
    assert processes[0].returncode is not None
    assert m2m100_cache_paths(manager.settings)[0].parent.name == first.job_id
    queued = await manager.start_m2m100_prepare()
    await manager.cancel_job(queued.job_id)
    await settle(manager)
    assert m2m100_cache_paths(manager.settings)[0].parent.name == first.job_id


async def test_cancel_during_late_spawn_retains_lease_and_old_cache_until_reaped(
    setup_manager, monkeypatch,
):
    manager, mode, processes = setup_manager
    initial = await manager.start_m2m100_prepare()
    await settle(manager)
    entered, release = asyncio.Event(), asyncio.Event()
    launch = asyncio.create_subprocess_exec

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await launch(*args, **kwargs)

    mode[0] = "hang"
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    job = await manager.start_m2m100_prepare()
    await entered.wait()
    await manager.cancel_job(job.job_id)
    assert manager.m2m100_preparing
    assert (await manager.start_m2m100_prepare()).job_id == job.job_id
    assert m2m100_cache_paths(manager.settings)[0].parent.name == initial.job_id
    release.set()
    await settle(manager)
    assert all(process.returncode is not None for process in processes)
    assert not (managed_m2m100_root(manager.settings) / job.job_id).exists()
    assert (await manager.get_job(job.job_id)).state == "cancelled"


async def test_prepare_timeout_keeps_old_pointer_with_fixed_public_error(
    setup_manager, monkeypatch,
):
    manager, mode, _ = setup_manager
    initial = await manager.start_m2m100_prepare()
    await settle(manager)
    mode[0] = "hang"
    monkeypatch.setattr("langtextflow.model_setup.PREPARE_TIMEOUT_SECONDS", .05)
    job = await manager.start_m2m100_prepare()
    await settle(manager)
    result = await manager.get_job(job.job_id)
    assert result.state == "error" and "http" not in result.error
    assert "private" not in result.model_dump_json()
    assert m2m100_cache_paths(manager.settings)[0].parent.name == initial.job_id


def test_native_setup_http_is_operator_only_and_excludes_session_and_clear(monkeypatch):
    from langtextflow import main

    manager = ModelSetupManager(Settings())
    monkeypatch.setattr(main, "model_setup_manager", manager)
    monkeypatch.setattr(main.runtime.state, "running", True)
    with TestClient(main.app, client=("127.0.0.1", 1234)) as client:
        assert client.post("/api/v1/setup/m2m100/prepare").status_code == 409
    monkeypatch.setattr(main.runtime.state, "running", False)
    manager._active_keys[("m2m100-ct2", prepare.MODEL_ID.casefold())] = "owned"
    with TestClient(main.app, client=("127.0.0.1", 1234)) as client:
        assert client.post("/api/v1/session/start", json={"engine": "mock"}).status_code == 409
        assert client.delete("/api/v1/setup/cache/m2m100-native").status_code == 409
    with TestClient(main.app, client=("192.168.1.2", 1234)) as client:
        assert client.post("/api/v1/setup/m2m100/prepare").status_code == 403


def test_prepare_reuses_verified_bytes_and_repairs_only_bad_files(tmp_path, monkeypatch):
    old = tmp_path / "old"
    payloads = {item.relative: item.relative.encode() for item in prepare.FILES}
    files = tuple(replace(item, sha256=hashlib.sha256(payloads[item.relative]).hexdigest(),
                          maximum_bytes=len(payloads[item.relative])) for item in prepare.FILES)
    monkeypatch.setattr(prepare, "FILES", files)
    for item in files:
        source = old / item.relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payloads[item.relative])
    calls = []

    def handler(request):
        calls.append(request.url.path)
        item = next(item for item in files if item.url == str(request.url))
        return httpx.Response(200, stream=Blocks(payloads[item.relative]))

    client = httpx.Client
    monkeypatch.setattr(prepare.httpx, "Client", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(handler)))
    loaded = []
    monkeypatch.setattr(mt.M2M100CT2Translator, "_prepare_sync",
                        lambda provider: loaded.append(provider.model_dir))
    stage = tmp_path / "first"
    stage.mkdir()
    prepare.prepare(stage, 0, (old / "model", old / "tokenizer"))
    assert not calls and loaded == [stage / "model"]
    (old / files[0].relative).write_bytes(b"bad")
    stage = tmp_path / "repair"
    stage.mkdir()
    prepare.prepare(stage, 0, (old / "model", old / "tokenizer"))
    assert calls == [httpx.URL(files[0].url).path]
    assert (stage / files[0].relative).read_bytes() == payloads[files[0].relative]


async def test_actual_prepare_child_parent_eof_stops_blocked_download(tmp_path):
    # Fake only HTTP/native boundary; run the shipped child and its EOF watcher unchanged.
    (tmp_path / "httpx.py").write_text('''
import pathlib, time
def Timeout(*args, **kwargs): return None
class Client:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def stream(self, *args, **kwargs):
        pathlib.Path(__file__).with_suffix('.entered').touch()
        time.sleep(60)
''')
    stage = tmp_path / "stage"
    stage.mkdir()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "langtextflow.m2m100_prepare_worker", str(stage), "0",
        str(tmp_path / "absent-model"), str(tmp_path / "absent-tokenizer"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([
            str(tmp_path), str(Path(__file__).parents[1]),
        ])},
    )
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / "httpx.entered").exists():
                await asyncio.sleep(.01)
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 3)
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("payload", [
    {"phase": ["downloading"], "file": "model/model.bin"},
    {"phase": "https://user:secret@host/private?token=secret", "file": "/private/cache"},
    {"phase": "downloading", "file": "https://user:secret@host/private", "pid": "secret"},
])
def test_progress_filters_untrusted_values_and_never_returns_locations(tmp_path, payload):
    (tmp_path / "phase.json").write_text(json.dumps(payload))
    result = prepare.preparation_progress(tmp_path)
    assert result in ({}, {"phase": "downloading"})
    assert not any(value in json.dumps(result) for value in ("secret", "http", "/private"))


def test_progress_exposes_only_fixed_phase_key_and_bounded_file_bytes(tmp_path):
    (tmp_path / "model").mkdir()
    (tmp_path / "model/config.json").write_bytes(b"verified")
    prepare._phase(tmp_path, "downloading", "model/config.json")
    assert prepare.preparation_progress(tmp_path) == {
        "phase": "downloading", "file": "model/config.json", "file_bytes": 8,
    }
