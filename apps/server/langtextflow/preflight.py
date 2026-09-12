from __future__ import annotations

import asyncio
import importlib.util
import os
import platform
import shutil
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .config import Settings

_FASTER_WHISPER_REPOS = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
_WHISPER_REQUIRED_FILES = {
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
}


class CheckStatus(StrEnum):
    READY = "ready"
    WARNING = "warning"
    MISSING = "missing"
    ERROR = "error"
    INFO = "info"


class PreflightCheck(BaseModel):
    id: str
    label: str
    status: CheckStatus
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    recommendation: str | None = None


class RecommendedConfiguration(BaseModel):
    engine: str | None
    translation_provider: str
    translation_model: str | None = None
    reasons: list[str] = Field(default_factory=list)


class SystemPreflight(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_engine: str
    translation_provider: str
    translation_model: str | None
    ready: bool
    blocking_checks: list[str] = Field(default_factory=list)
    platform: str
    architecture: str
    python_version: str
    cpu_count: int | None
    memory_gb: float | None
    disk_free_gb: float | None
    checks: list[PreflightCheck]
    recommended: RecommendedConfiguration


def _windows_memory_gb() -> float | None:
    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return round(status.ullTotalPhys / (1024**3), 1)
    except (AttributeError, OSError, ValueError):
        return None


def _memory_gb() -> float | None:
    windows_value = _windows_memory_gb()
    if windows_value is not None:
        return windows_value
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
        return round((page_size * pages) / (1024**3), 1)
    except (AttributeError, OSError, ValueError):
        return None


def _disk_free_gb(database_path: str) -> float | None:
    try:
        path = Path(database_path).expanduser().resolve()
        probe = path.parent
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        return round(shutil.disk_usage(probe).free / (1024**3), 1)
    except OSError:
        return None


def _nvidia_smi_path() -> str | None:
    executable = shutil.which("nvidia-smi")
    if executable is not None:
        return executable
    if platform.system() != "Windows":
        return None
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "NVIDIA Corporation"
        / "NVSMI"
        / "nvidia-smi.exe",
    ]
    return next((str(candidate) for candidate in candidates if candidate.is_file()), None)


def _nvidia_check() -> PreflightCheck:
    executable = _nvidia_smi_path()
    if executable is None:
        apple_silicon = platform.system() == "Darwin" and platform.machine().lower() in {
            "arm64",
            "aarch64",
        }
        return PreflightCheck(
            id="nvidia",
            label="GPU",
            status=CheckStatus.INFO,
            summary=(
                "Apple Silicon unified GPU 환경입니다."
                if apple_silicon
                else "NVIDIA GPU를 확인하지 못했습니다."
            ),
            recommendation=(
                "실제 모델 benchmark 결과를 기준으로 로컬 provider를 선택하세요."
                if apple_silicon
                else "CPU 또는 다른 가속기 실행도 가능하며 NVIDIA는 필수 조건이 아닙니다."
            ),
            details={"apple_silicon": apple_silicon},
        )
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return PreflightCheck(
            id="nvidia",
            label="NVIDIA GPU",
            status=CheckStatus.WARNING,
            summary="nvidia-smi 실행에 실패했습니다.",
            details={"error": str(exc)},
            recommendation="GPU 드라이버 상태를 확인하세요.",
        )

    devices: list[dict[str, str | int | None]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            memory_mb: int | None = int(parts[1])
        except ValueError:
            memory_mb = None
        devices.append(
            {"name": parts[0], "memory_mb": memory_mb, "driver_version": parts[2]}
        )
    return PreflightCheck(
        id="nvidia",
        label="NVIDIA GPU",
        status=CheckStatus.READY if devices else CheckStatus.WARNING,
        summary=(
            f"NVIDIA GPU {len(devices)}개를 확인했습니다."
            if devices
            else "nvidia-smi는 있지만 GPU 정보를 읽지 못했습니다."
        ),
        details={"devices": devices, "executable": executable},
    )


def _faster_whisper_checks(model: str) -> list[PreflightCheck]:
    installed = importlib.util.find_spec("faster_whisper") is not None
    package_check = PreflightCheck(
        id="faster-whisper",
        label="faster-whisper",
        status=CheckStatus.READY if installed else CheckStatus.MISSING,
        summary=(
            "faster-whisper Python 패키지가 설치되어 있습니다."
            if installed
            else "faster-whisper Python 패키지가 설치되어 있지 않습니다."
        ),
        recommendation=(
            None
            if installed
            else "Auto fallback을 사용하려면 LangTextFlow의 whisper 선택 의존성을 설치하세요."
        ),
    )
    if not installed:
        return [
            package_check,
            PreflightCheck(
                id="faster-whisper-model",
                label="faster-whisper 모델",
                status=CheckStatus.MISSING,
                summary=f"{model} 모델 cache를 확인할 수 없습니다.",
                details={"model": model},
                recommendation="먼저 faster-whisper runtime을 설치하세요.",
            ),
        ]

    local_path = Path(model).expanduser()
    if local_path.is_dir():
        missing = sorted(
            filename
            for filename in _WHISPER_REQUIRED_FILES
            if not (local_path / filename).is_file()
        )
        return [
            package_check,
            PreflightCheck(
                id="faster-whisper-model",
                label="faster-whisper 모델",
                status=CheckStatus.READY if not missing else CheckStatus.MISSING,
                summary=(
                    f"로컬 모델 {local_path}이 준비되어 있습니다."
                    if not missing
                    else f"로컬 모델에 필수 파일 {len(missing)}개가 없습니다."
                ),
                details={"model": model, "path": str(local_path), "missing": missing},
            ),
        ]

    repo_id = model if "/" in model else _FASTER_WHISPER_REPOS.get(model)
    if repo_id is None:
        return [
            package_check,
            PreflightCheck(
                id="faster-whisper-model",
                label="faster-whisper 모델",
                status=CheckStatus.MISSING,
                summary=f"지원되는 faster-whisper model 이름이 아닙니다: {model}",
                details={"model": model},
            ),
        ]

    try:
        from huggingface_hub import snapshot_download

        cached_path = Path(
            snapshot_download(
                repo_id,
                local_files_only=True,
                allow_patterns=[
                    "config.json",
                    "preprocessor_config.json",
                    "model.bin",
                    "tokenizer.json",
                    "vocabulary.*",
                ],
            )
        )
        missing = sorted(
            filename
            for filename in _WHISPER_REQUIRED_FILES
            if not (cached_path / filename).is_file()
        )
        if missing:
            raise FileNotFoundError(
                "cached snapshot is incomplete: " + ", ".join(missing)
            )
    except Exception as exc:
        return [
            package_check,
            PreflightCheck(
                id="faster-whisper-model",
                label="faster-whisper 모델",
                status=CheckStatus.MISSING,
                summary=f"{model} 모델이 로컬 cache에 준비되어 있지 않습니다.",
                details={"model": model, "repo_id": repo_id, "error": str(exc)},
                recommendation="세션 시작 전에 모델을 다운로드하세요.",
            ),
        ]

    return [
        package_check,
        PreflightCheck(
            id="faster-whisper-model",
            label="faster-whisper 모델",
            status=CheckStatus.READY,
            summary=f"{model} 모델이 로컬 cache에 준비되어 있습니다.",
            details={"model": model, "repo_id": repo_id, "path": str(cached_path)},
        ),
    ]


async def _vibevoice_check(settings: Settings) -> PreflightCheck:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.get(f"{settings.vibevoice_url.rstrip('/')}/v1/config")
            response.raise_for_status()
            raw_payload = response.json()
            payload = raw_payload if isinstance(raw_payload, dict) else {}
    except Exception as exc:
        return PreflightCheck(
            id="vibevoice",
            label="VibeVoice Streaming",
            status=CheckStatus.MISSING,
            summary="VibeVoice sidecar에 연결할 수 없습니다.",
            details={"url": settings.vibevoice_url, "error": str(exc)},
            recommendation="VibeVoice sidecar를 실행하거나 Auto 모드에서 fallback을 사용하세요.",
        )
    return PreflightCheck(
        id="vibevoice",
        label="VibeVoice Streaming",
        status=CheckStatus.READY,
        summary="VibeVoice streaming sidecar가 응답합니다.",
        details={
            "url": settings.vibevoice_url,
            "sample_rate": payload.get("sample_rate"),
            "chunk_seconds": payload.get("chunk_seconds"),
        },
    )


async def _ollama_checks(settings: Settings, model: str) -> list[PreflightCheck]:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.get(f"{settings.ollama_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            raw_payload = response.json()
            payload = raw_payload if isinstance(raw_payload, dict) else {}
    except Exception as exc:
        return [
            PreflightCheck(
                id="ollama",
                label="Ollama",
                status=CheckStatus.MISSING,
                summary="Ollama에 연결할 수 없습니다.",
                details={"url": settings.ollama_url, "error": str(exc)},
                recommendation="로컬 번역을 사용하려면 Ollama를 실행하세요.",
            ),
            PreflightCheck(
                id="translation-model",
                label="번역 모델",
                status=CheckStatus.MISSING,
                summary=f"{model} 설치 여부를 확인할 수 없습니다.",
            ),
        ]

    raw_models = payload.get("models", [])
    models = raw_models if isinstance(raw_models, list) else []
    names = [str(item.get("name", "")) for item in models if isinstance(item, dict)]
    normalized = model.casefold()
    available = any(
        name.casefold() == normalized
        or name.casefold().removesuffix(":latest") == normalized.removesuffix(":latest")
        for name in names
    )
    summary = (
        f"{model} 모델이 준비되어 있습니다."
        if available
        else f"{model} 모델이 없습니다."
    )
    return [
        PreflightCheck(
            id="ollama",
            label="Ollama",
            status=CheckStatus.READY,
            summary="Ollama 로컬 API가 응답합니다.",
            details={"url": settings.ollama_url, "models": len(names)},
        ),
        PreflightCheck(
            id="translation-model",
            label="번역 모델",
            status=CheckStatus.READY if available else CheckStatus.MISSING,
            summary=summary,
            details={"model": model},
            recommendation=None if available else f"Ollama에서 {model} 모델을 내려받으세요.",
        ),
    ]


def _is_ready(check: PreflightCheck) -> bool:
    return check.status is CheckStatus.READY


def _whisper_ready(indexed: dict[str, PreflightCheck]) -> bool:
    return _is_ready(indexed["faster-whisper"]) and _is_ready(
        indexed["faster-whisper-model"]
    )


def _blocking_checks(
    checks: list[PreflightCheck],
    *,
    engine: str,
    translation_provider: str,
) -> list[str]:
    indexed = {check.id: check for check in checks}
    blocking: list[str] = []
    vibevoice_ready = _is_ready(indexed["vibevoice"])
    whisper_ready = _whisper_ready(indexed)

    if engine == "vibevoice" and not vibevoice_ready:
        blocking.append("vibevoice")
    elif engine == "faster-whisper" and not whisper_ready:
        if not _is_ready(indexed["faster-whisper"]):
            blocking.append("faster-whisper")
        elif not _is_ready(indexed["faster-whisper-model"]):
            blocking.append("faster-whisper-model")
    elif engine == "auto" and not (vibevoice_ready or whisper_ready):
        blocking.append("vibevoice")
        if not _is_ready(indexed["faster-whisper"]):
            blocking.append("faster-whisper")
        elif not _is_ready(indexed["faster-whisper-model"]):
            blocking.append("faster-whisper-model")
    elif engine not in {"auto", "vibevoice", "faster-whisper", "mock"}:
        blocking.append("engine")

    if translation_provider == "ollama":
        if not _is_ready(indexed["ollama"]):
            blocking.append("ollama")
        if not _is_ready(indexed["translation-model"]):
            blocking.append("translation-model")
    elif translation_provider not in {"none", "demo"}:
        blocking.append("translation-provider")

    return list(dict.fromkeys(blocking))


def _recommended_configuration(
    checks: list[PreflightCheck],
    *,
    model: str,
) -> RecommendedConfiguration:
    indexed = {check.id: check for check in checks}
    vibevoice_ready = _is_ready(indexed["vibevoice"])
    whisper_ready = _whisper_ready(indexed)
    reasons: list[str] = []

    if vibevoice_ready and whisper_ready:
        engine: str | None = "auto"
        reasons.append(
            "VibeVoice와 faster-whisper model cache가 모두 준비되어 있어 "
            "Auto 복구 경로를 사용할 수 있습니다."
        )
    elif vibevoice_ready:
        engine = "vibevoice"
        reasons.append("VibeVoice만 즉시 실행 가능한 상태이므로 단독 사용을 권장합니다.")
    elif whisper_ready:
        engine = "faster-whisper"
        reasons.append("준비된 로컬 faster-whisper를 사용할 수 있습니다.")
    else:
        engine = None
        reasons.append(
            "사용 가능한 실제 ASR provider가 없습니다. "
            "모델 준비 또는 sidecar 실행이 필요합니다."
        )

    ollama_ready = _is_ready(indexed["ollama"])
    translation_model_ready = _is_ready(indexed["translation-model"])
    if ollama_ready and translation_model_ready:
        translation_provider = "ollama"
        translation_model: str | None = model
        reasons.append(f"Ollama와 {model}이 준비되어 있어 로컬 번역을 사용할 수 있습니다.")
    else:
        translation_provider = "none"
        translation_model = None
        reasons.append("로컬 번역 환경이 완전하지 않아 원문 자막 우선 구성을 권장합니다.")

    return RecommendedConfiguration(
        engine=engine,
        translation_provider=translation_provider,
        translation_model=translation_model,
        reasons=reasons,
    )


async def run_preflight(
    settings: Settings,
    translation_model: str | None = None,
    engine: str = "auto",
    translation_provider: str = "ollama",
) -> SystemPreflight:
    model = translation_model or settings.ollama_translation_model
    nvidia, whisper, vibevoice, ollama = await asyncio.gather(
        asyncio.to_thread(_nvidia_check),
        asyncio.to_thread(_faster_whisper_checks, settings.faster_whisper_model),
        _vibevoice_check(settings),
        _ollama_checks(settings, model),
    )
    memory_gb = _memory_gb()
    disk_free_gb = _disk_free_gb(settings.database_path)
    checks: list[PreflightCheck] = [nvidia, *whisper, vibevoice, *ollama]
    if memory_gb is not None:
        checks.append(
            PreflightCheck(
                id="memory",
                label="시스템 메모리",
                status=CheckStatus.READY if memory_gb >= 16 else CheckStatus.WARNING,
                summary=f"총 메모리 약 {memory_gb:.1f} GB",
                recommendation=(
                    None
                    if memory_gb >= 16
                    else "16 GB 미만에서는 큰 ASR/번역 모델 동시 실행을 피하세요."
                ),
            )
        )
    if disk_free_gb is not None:
        recommendation = (
            None if disk_free_gb >= 20 else "모델 설치 전 저장 공간을 확보하세요."
        )
        checks.append(
            PreflightCheck(
                id="disk",
                label="여유 저장 공간",
                status=CheckStatus.READY if disk_free_gb >= 20 else CheckStatus.WARNING,
                summary=f"약 {disk_free_gb:.1f} GB 사용 가능",
                recommendation=recommendation,
            )
        )

    blocking = _blocking_checks(
        checks,
        engine=engine,
        translation_provider=translation_provider,
    )
    return SystemPreflight(
        requested_engine=engine,
        translation_provider=translation_provider,
        translation_model=model if translation_provider == "ollama" else None,
        ready=not blocking,
        blocking_checks=blocking,
        platform=platform.platform(),
        architecture=platform.machine() or "unknown",
        python_version=platform.python_version(),
        cpu_count=os.cpu_count(),
        memory_gb=memory_gb,
        disk_free_gb=disk_free_gb,
        checks=checks,
        recommended=_recommended_configuration(checks, model=model),
    )
