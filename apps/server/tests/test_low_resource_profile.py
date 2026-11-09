"""CPU-only low-resource runtime: no accidental 7B startup, consistent preflight."""
from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from langtextflow.asr.fallback import ReplayFallbackAsrEngine
from langtextflow.asr.faster_whisper import FasterWhisperStreamingAsrEngine
from langtextflow.config import Settings
from langtextflow.models import StartSessionRequest
from langtextflow.preflight import (
    CheckStatus,
    PreflightCheck,
    _blocking_checks,
    _recommended_configuration,
    run_preflight,
)
from langtextflow.runtime import CaptionRuntime


def check(name: str, state: CheckStatus) -> PreflightCheck:
    return PreflightCheck(id=name, label=name, status=state, summary=name)


def ready() -> list[PreflightCheck]:
    return [
        check("vibevoice", CheckStatus.READY),
        check("faster-whisper", CheckStatus.READY),
        check("faster-whisper-model", CheckStatus.READY),
        check("ollama", CheckStatus.READY),
        check("translation-model", CheckStatus.READY),
    ]


def test_low_profile_auto_never_probes_7b_and_caps_cpu_resources(tmp_path) -> None:
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "low.sqlite"),
        resource_profile="low", low_resource_whisper_model="base",
        low_resource_cpu_threads=2, audio_queue_chunks=32,
    )
    runtime = CaptionRuntime(settings)
    engine = runtime._build_engine(StartSessionRequest(engine="auto"))
    assert type(engine) is FasterWhisperStreamingAsrEngine
    assert engine.model_name == "base"
    assert engine.device == "cpu"
    assert engine.compute_type == "int8"
    assert engine.cpu_threads == 2
    assert engine.queue_capacity == 8
    assert settings.active_whisper_model == "base"


def test_low_profile_respects_stricter_queue_limit(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(
        _env_file=None, database_path=str(tmp_path / "low.sqlite"),
        resource_profile="low", audio_queue_chunks=3,
    ))
    engine = runtime._build_engine(StartSessionRequest(engine="faster-whisper"))
    assert isinstance(engine, FasterWhisperStreamingAsrEngine)
    assert engine.queue_capacity == 3


def test_standard_profile_preserves_existing_vibevoice_fallback(tmp_path) -> None:
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "standard.sqlite"),
        resource_profile="standard", faster_whisper_model="small",
    )
    runtime = CaptionRuntime(settings)
    auto = runtime._build_engine(StartSessionRequest(engine="auto"))
    assert isinstance(auto, ReplayFallbackAsrEngine)
    direct = runtime._build_engine(StartSessionRequest(engine="faster-whisper"))
    assert isinstance(direct, FasterWhisperStreamingAsrEngine)
    assert direct.model_name == "small"
    assert direct.cpu_threads is None
    assert settings.active_whisper_model == "small"


def test_low_profile_preflight_prefers_cpu_only_even_if_vibevoice_is_ready() -> None:
    recommended = _recommended_configuration(
        ready(), model="translategemma:4b", low_resource=True,
    )
    assert recommended.engine == "faster-whisper"
    assert recommended.translation_provider == "none"
    assert recommended.translation_model is None
    assert _blocking_checks(
        ready(), engine="auto", translation_provider="none", low_resource=True,
    ) == []


def test_low_auto_blocks_when_only_7b_model_is_ready() -> None:
    checks = [check("vibevoice", CheckStatus.READY),
              check("faster-whisper", CheckStatus.READY),
              check("faster-whisper-model", CheckStatus.MISSING)]
    assert _blocking_checks(
        checks, engine="auto", translation_provider="none", low_resource=True,
    ) == ["faster-whisper-model"]
    assert _recommended_configuration(
        checks, model="translategemma:4b", low_resource=True,
    ).engine is None


def test_resource_profile_is_strictly_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, resource_profile="ultra")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, low_resource_cpu_threads=0)


def test_environment_can_enable_church_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGTEXTFLOW_RESOURCE_PROFILE", "low")
    monkeypatch.setenv("LANGTEXTFLOW_LOW_RESOURCE_WHISPER_MODEL", "tiny")
    monkeypatch.setenv("LANGTEXTFLOW_LOW_RESOURCE_CPU_THREADS", "1")
    settings = Settings(_env_file=None)
    assert settings.resource_profile == "low"
    assert settings.active_whisper_model == "tiny"
    assert settings.low_resource_cpu_threads == 1



def test_operator_selecting_low_profile_works_without_environment_variable(tmp_path) -> None:
    settings = Settings(
        _env_file=None, database_path=str(tmp_path / "session.sqlite"),
        resource_profile="standard",
    )
    runtime = CaptionRuntime(settings)
    engine = runtime._build_engine(
        StartSessionRequest(engine="auto", resource_profile="low")
    )
    assert type(engine) is FasterWhisperStreamingAsrEngine
    assert engine.device == "cpu"
    assert engine.model_name == "base"
    assert engine.compute_type == "int8"
    assert engine.queue_capacity == 8


def test_bad_per_session_resource_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StartSessionRequest(resource_profile="unverified")


@pytest.mark.parametrize("engine,profile,server_profile,probes", [
    ("auto", "low", "standard", 0), ("auto", "standard", "low", 0),
    ("auto", "standard", "standard", 1), ("vibevoice", "low", "low", 1),
])
async def test_full_preflight_low_auto_never_requests_sidecar_configuration(
    monkeypatch, engine, profile, server_profile, probes,
):
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(str(request.url))
        if request.url.host == "sidecar.test":
            return httpx.Response(200, json={"sample_rate": 48000, "chunk_seconds": 2.})
        return httpx.Response(200, json={"models": [], "data": []})

    def client(*args, **kwargs):
        return original_client(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("langtextflow.preflight.httpx.AsyncClient", client)
    monkeypatch.setattr(
        "langtextflow.preflight._nvidia_check", lambda: check("nvidia", CheckStatus.INFO),
    )
    monkeypatch.setattr("langtextflow.preflight._faster_whisper_checks", lambda *args: [
        check("faster-whisper", CheckStatus.READY),
        check("faster-whisper-model", CheckStatus.READY),
    ])
    monkeypatch.setattr("langtextflow.preflight._memory_gb", lambda: 16.)
    monkeypatch.setattr("langtextflow.preflight._disk_free_gb", lambda *_: 10.)
    report = await run_preflight(Settings(
        _env_file=None, resource_profile=server_profile, vibevoice_url="http://sidecar.test",
        ollama_url="http://ollama.test", openai_compatible_url="http://compatible.test/v1",
    ), engine=engine, resource_profile=profile, translation_provider="none")
    assert sum("sidecar.test" in url for url in requests) == probes
    sidecar = next(c for c in report.checks if c.id == "vibevoice")
    assert sidecar.status is (CheckStatus.READY if probes else CheckStatus.INFO)
    assert report.ready
