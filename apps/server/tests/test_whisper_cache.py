"""Preparation, preflight and live/offline consumers share local-only Whisper lookup."""

from __future__ import annotations

import importlib.machinery
import json
import sys
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from langtextflow import preflight, whisper_prefetch_worker
from langtextflow.asr.faster_whisper import FasterWhisperStreamingAsrEngine
from langtextflow.benchmark import build_asr_engine
from langtextflow.config import Settings
from langtextflow.model_cache import ModelCacheManager
from langtextflow.model_setup import ModelSetupManager, SetupJobState
from langtextflow.runtime import CaptionRuntime
from langtextflow.whisper_cache import (
    WHISPER_REQUIRED_FILES,
    managed_whisper_cache,
    prepare_whisper_model,
    resolve_whisper_model,
)


def cached(root: Path, model: str = "small") -> Path:
    repo = root / f"models--Systran--faster-whisper-{model}"
    path = repo / "snapshots" / ("a" * 40)
    path.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("a" * 40)
    for name in WHISPER_REQUIRED_FILES:
        (path / name).write_bytes(b"fixture")
    return path.resolve()


@pytest.fixture
def cache_environment(monkeypatch, tmp_path):
    roots = {
        "managed": tmp_path / "managed" / "huggingface",
        "hf_home": tmp_path / "hf-home",
        "captured": tmp_path / "captured-hub-default",
        "legacy": tmp_path / "xdg" / "huggingface",
        "explicit": tmp_path / "explicit-hub",
    }
    monkeypatch.setenv("HF_HOME", str(roots["hf_home"]))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    looked_up, downloads, loaded = [], [], []

    def snapshot(repo, **options):
        looked_up.append(options)
        assert options["local_files_only"] is True
        folder = Path(options["cache_dir"]) / ("models--" + repo.replace("/", "--"))
        revision = (folder / "refs" / "main").read_text()
        return str(folder / "snapshots" / revision)

    def download(model, *, cache_dir):
        downloads.append((model, cache_dir))
        return str(cached(Path(cache_dir) if cache_dir else roots["captured"], model))

    class Model:
        def __init__(self, path, **options):
            loaded.append((path, options))

        def transcribe(self, *args, **kwargs):
            return iter(
                [
                    SimpleNamespace(
                        text="fixture",
                        start=0.0,
                        end=1.0,
                        avg_logprob=0.0,
                        no_speech_prob=0.0,
                    )
                ]
            ), None

    hub = ModuleType("huggingface_hub")
    hub.constants = SimpleNamespace(HF_HUB_CACHE=str(roots["captured"]))
    hub.snapshot_download = snapshot
    whisper = ModuleType("faster_whisper")
    whisper.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", loader=None)
    whisper.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "faster_whisper", whisper)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper.utils",
        SimpleNamespace(download_model=download),
    )
    return SimpleNamespace(roots=roots, lookups=looked_up, downloads=downloads, loaded=loaded)


@pytest.mark.parametrize(
    "layout",
    [
        "managed",
        "managed-hub",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "hf-home-hub",
        "hf-home-direct",
        "captured",
        "legacy-hub",
        "legacy-direct",
    ],
)
def test_reads_managed_configured_and_legacy_caches_without_downloads(
    monkeypatch,
    cache_environment,
    layout,
):
    env = cache_environment
    if layout in {"HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"}:
        monkeypatch.setenv(layout, str(env.roots["explicit"]))
        root = env.roots["explicit"]
    else:
        root = {
            "managed": env.roots["managed"],
            "managed-hub": env.roots["managed"] / "hub",
            "hf-home-hub": env.roots["hf_home"] / "hub",
            "hf-home-direct": env.roots["hf_home"],
            "captured": env.roots["captured"],
            "legacy-hub": env.roots["legacy"] / "hub",
            "legacy-direct": env.roots["legacy"],
        }[layout]
    expected = cached(root)
    assert resolve_whisper_model("small", env.roots["managed"]) == expected
    assert prepare_whisper_model("small", env.roots["managed"]) == expected
    assert not env.downloads


def test_managed_prepare_preflight_and_runtime_factory_agree(cache_environment, tmp_path):
    env = cache_environment
    expected = cached(env.roots["managed"])
    settings = Settings(
        model_cache_dir=str(env.roots["managed"].parent),
        database_path=str(tmp_path / "history.db"),
        faster_whisper_model="small",
    )
    check = preflight._faster_whisper_checks(
        "small",
        managed_whisper_cache(settings.model_cache_dir),
    )[-1]
    assert check.status is preflight.CheckStatus.READY and check.details["path"] == str(expected)
    runtime = CaptionRuntime(settings)
    engine = runtime._faster_whisper_engine()
    engine._load_model_sync()
    assert env.loaded[0][0] == str(expected)
    assert env.loaded[0][1]["local_files_only"] is True
    assert not engine.allow_download and not env.downloads
    assert not (expected / "preprocessor_config.json").exists()  # Official legacy conversions work.


@pytest.mark.parametrize("missing", WHISPER_REQUIRED_FILES)
def test_incomplete_model_cannot_reach_native_loader_or_download(cache_environment, missing):
    env = cache_environment
    path = cached(env.roots["managed"])
    (path / missing).unlink()

    async def publish(event):
        pass

    engine = FasterWhisperStreamingAsrEngine(publish, cache_dir=str(env.roots["managed"]))
    with pytest.raises(FileNotFoundError):
        engine._load_model_sync()
    check = preflight._faster_whisper_checks("small", env.roots["managed"])[-1]
    assert check.status is preflight.CheckStatus.MISSING
    assert not env.loaded and not env.downloads


def test_explicit_local_directory_wins_and_incomplete_directory_is_not_a_repo(cache_environment):
    env = cache_environment
    path = cached(env.roots["explicit"])
    assert resolve_whisper_model(str(path), env.roots["managed"]) == path
    assert not env.lookups
    (path / "tokenizer.json").unlink()
    with pytest.raises(FileNotFoundError, match="tokenizer"):
        prepare_whisper_model(str(path), env.roots["managed"])
    assert not env.downloads


def test_only_explicit_benchmark_bootstrap_may_download(cache_environment):
    env = cache_environment

    async def publish(event):
        pass

    engine = build_asr_engine("faster-whisper", publish, Settings(faster_whisper_model="small"))
    assert engine.allow_download
    engine._load_model_sync()
    assert env.downloads == [("small", None)]
    assert env.loaded[0][1]["local_files_only"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("complete", [False, True])
async def test_prefetch_uses_explicit_worker_and_requires_complete_cache(
    monkeypatch,
    cache_environment,
    frozen,
    complete,
):
    from langtextflow import model_setup

    env = cache_environment
    prepared = cached(env.roots["managed"])
    if not complete:
        (prepared / "tokenizer.json").unlink()
    launched = []

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps({"path": str(prepared)}).encode(), b""

    async def spawn(*arguments, **options):
        launched.append(arguments)
        return Process()

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(model_setup.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(model_setup, "ensure_storage_capacity", lambda *args, **kwargs: None)
    manager = ModelSetupManager(Settings(model_cache_dir=str(env.roots["managed"].parent)))
    job = await manager.start_faster_whisper_prefetch("small")
    await manager._tasks[job.job_id]
    finished = await manager.get_job(job.job_id)
    assert finished.state is (SetupJobState.COMPLETED if complete else SetupJobState.ERROR)
    if complete:
        assert finished.details["path"] == str(prepared)
    assert "-c" not in launched[0]
    assert launched[0][1:-2] == (
        ("--faster-whisper-prefetch-worker",)
        if frozen
        else ("-m", "langtextflow.whisper_prefetch_worker")
    )


def test_prefetch_worker_invalid_arguments_are_quiet_and_never_download(cache_environment, capsys):
    for args in ([], ["small"], ["invalid-model", "unused-cache"]):
        with pytest.raises(SystemExit) as stopped:
            whisper_prefetch_worker.main(args)
        assert stopped.value.code == 1
    assert capsys.readouterr() == ("", "")
    assert not cache_environment.downloads


def test_frozen_prefetch_dispatch_is_after_freeze_support_before_web_setup(monkeypatch):
    from langtextflow import desktop_server

    seen = []
    monkeypatch.setattr(
        sys, "argv", ["binary", "--faster-whisper-prefetch-worker", "small", "cache"]
    )
    monkeypatch.setattr(
        desktop_server.multiprocessing, "freeze_support", lambda: seen.append("freeze")
    )
    monkeypatch.setattr(whisper_prefetch_worker, "main", seen.append)
    monkeypatch.setattr(
        desktop_server, "_prepare_desktop_environment", lambda: pytest.fail("web setup")
    )
    desktop_server.main()
    assert seen == ["freeze", ["small", "cache"]]


def test_cached_preparation_does_not_reserve_space_for_another_download(
    monkeypatch,
    cache_environment,
):
    from langtextflow import model_setup

    env = cache_environment
    cached(env.roots["managed"])
    monkeypatch.setattr(
        model_setup, "ensure_storage_capacity", lambda *args, **kw: pytest.fail("download reserve")
    )
    ModelSetupManager(
        Settings(
            model_cache_dir=str(env.roots["managed"].parent),
        )
    )._ensure_initial_storage("faster-whisper", "small")


def test_prefetch_worker_reuses_complete_legacy_model(cache_environment, capsys):
    env = cache_environment
    path = cached(env.roots["captured"])
    whisper_prefetch_worker.main(["small", str(env.roots["managed"])])
    assert json.loads(capsys.readouterr().out) == {"path": str(path)}
    assert not env.downloads


def test_offline_transcript_worker_uses_same_managed_snapshot(
    monkeypatch,
    cache_environment,
    tmp_path,
    capsys,
):
    from langtextflow import post_event_transcript_worker as worker

    env = cache_environment
    expected = cached(env.roots["managed"])
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0" * 32000)
    with audio.open("rb") as source:
        digest = worker._sha(source)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper.audio",
        SimpleNamespace(
            decode_audio=lambda buffer: buffer.getvalue(),
        ),
    )
    monkeypatch.setattr(worker, "version", lambda _: "fake-test")
    worker.decode(audio, "small", "ko", digest, str(env.roots["managed"]))
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert messages[-2] == {"progress": 1.0}
    assert "complete" in messages[-1]
    assert env.loaded[0][0] == str(expected) and env.loaded[0][1]["local_files_only"]
    assert not env.downloads


@pytest.mark.asyncio
async def test_clear_only_removes_managed_cache_preserving_external_legacy_models(
    cache_environment,
):
    env = cache_environment
    owned = cached(env.roots["managed"])
    external = cached(env.roots["captured"])
    manager = ModelCacheManager(Settings(model_cache_dir=str(env.roots["managed"].parent)))
    await manager.clear("huggingface")
    assert not owned.exists() and external.is_dir()
    assert resolve_whisper_model("small", env.roots["managed"]) == external
