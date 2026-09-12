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


def _memory_gb() -> float | None:
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


def _nvidia_check() -> PreflightCheck:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return PreflightCheck(
            id="nvidia",
            label="NVIDIA GPU",
            status=CheckStatus.INFO,
            summary="NVIDIA GPU를 확인하지 못했습니다.",
            recommendation="Apple Silicon 또는 CPU 실행도 가능하며, NVIDIA 가속은 선택 사항입니다.",
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
        details={"devices": devices},
    )


def _faster_whisper_check() -> PreflightCheck:
    installed = importlib.util.find_spec("faster_whisper") is not None
    return PreflightCheck(
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
            summary=(f"{model} 모델이 준비되어 있습니다." if available else f"{model} 모델이 없습니다."),
            details={"model": model},
            recommendation=None if available else f"Ollama에서 {model} 모델을 내려받으세요.",
        ),
    ]


def _is_ready(check: PreflightCheck) -> bool:
    return check.status is CheckStatus.READY


def _blocking_checks(
    checks: list[PreflightCheck],
    *,
    engine: str,
    translation_provider: str,
) -> list[str]:
    indexed = {check.id: check for check in checks}
    blocking: list[str] = []

    if engine == "vibevoice" and not _is_ready(indexed["vibevoice"]):
        blocking.append("vibevoice")
    elif engine == "faster-whisper" and not _is_ready(indexed["faster-whisper"]):
        blocking.append("faster-whisper")
    elif engine == "auto" and not (
        _is_ready(indexed["vibevoice"]) or _is_ready(indexed["faster-whisper"])
    ):
        blocking.extend(["vibevoice", "faster-whisper"])
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


async def run_preflight(
    settings: Settings,
    *,
    engine: str = "auto",
    translation_provider: str = "ollama",
    translation_model: str | None = None,
) -> SystemPreflight:
    model = translation_model or settings.ollama_translation_model
    nvidia, vibevoice, ollama = await asyncio.gather(
        asyncio.to_thread(_nvidia_check),
        _vibevoice_check(settings),
        _ollama_checks(settings, model),
    )
    memory_gb = _memory_gb()
    disk_free_gb = _disk_free_gb(settings.database_path)
    checks: list[PreflightCheck] = [nvidia, _faster_whisper_check(), vibevoice, *ollama]
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
        checks.append(
            PreflightCheck(
                id="disk",
                label="여유 저장 공간",
                status=CheckStatus.READY if disk_free_gb >= 20 else CheckStatus.WARNING,
                summary=f"약 {disk_free_gb:.1f} GB 사용 가능",
                recommendation=None if disk_free_gb >= 20 else "모델 설치 전 저장 공간을 확보하세요.",
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
    )
