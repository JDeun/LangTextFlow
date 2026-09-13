from __future__ import annotations

import io
import json
import os
import platform
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import Settings
from .models import SessionState
from .telemetry import RealtimeMetrics

_DIAGNOSTICS_SCHEMA_VERSION = 1
_REDACTED_SECRET = "<redacted-secret>"

_HOME_PREFIX_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_.-])/(?:home|Users)/[^/\\\s\"']+"),
    re.compile(r"(?i)(?<![A-Za-z0-9_.-])[A-Z]:\\Users\\[^\\/\s\"']+"),
    re.compile(r"(?i)(?<![A-Za-z0-9_.-])/mnt/[A-Z]/Users/[^/\\\s\"']+"),
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _model_dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _redact_path_string(value: str) -> str:
    """Remove user-identifying home-directory prefixes from diagnostic strings."""

    normalized = value
    home = str(Path.home())
    if home and home != "/":
        normalized = normalized.replace(home, "~")

    for pattern in _HOME_PREFIX_PATTERNS:
        normalized = pattern.sub("~", normalized)
    return normalized


def _sanitize_string(value: str, *, secrets: tuple[str, ...]) -> str:
    normalized = _redact_path_string(value)
    for secret in secrets:
        if secret:
            normalized = normalized.replace(secret, _REDACTED_SECRET)
    for pattern in _SECRET_PATTERNS:
        normalized = pattern.sub(_REDACTED_SECRET, normalized)
    return normalized


def _sanitize_diagnostics(value: Any, *, secrets: tuple[str, ...]) -> Any:
    """Recursively remove user paths and credential-like values from support metadata."""

    if isinstance(value, dict):
        return {
            str(key): _sanitize_diagnostics(item, secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_diagnostics(item, secrets=secrets) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_diagnostics(item, secrets=secrets) for item in value]
    if not isinstance(value, str):
        return value
    return _sanitize_string(value, secrets=secrets)


def _safe_session_state(state: SessionState) -> dict[str, Any]:
    context = state.context
    safe_context: dict[str, Any] | None = None
    if context is not None:
        safe_context = {
            "preset": context.preset.value,
            "hotword_count": len(context.hotwords),
            "glossary_count": len(context.glossary),
            "reference_document_count": len(context.reference_documents),
            "reference_documents": [
                {
                    "media_type": item.media_type,
                    "size_bytes": item.size_bytes,
                    "character_count": item.character_count,
                    "truncated": item.truncated,
                    "sha256_prefix": item.sha256[:12] if item.sha256 else "",
                }
                for item in context.reference_documents
            ],
            "output_modes": [mode.value for mode in context.output_modes],
            "audience_access": context.audience_access,
            "display_settings": context.display_settings.model_dump(mode="json"),
        }

    return {
        "running": state.running,
        "source_language": state.source_language,
        "target_languages": list(state.target_languages),
        "engine": state.engine,
        "audio_required": state.audio_required,
        "audio_sample_rate": state.audio_sample_rate,
        "correction_status": state.correction_status.model_dump(mode="json"),
        "translation_status": state.translation_status.model_dump(mode="json"),
        "persistence_error": state.persistence_error,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "context": safe_context,
    }


def _safe_settings(settings: Settings) -> dict[str, Any]:
    return {
        "environment": settings.environment,
        "frontend_port": settings.frontend_port,
        "backend_port": settings.backend_port,
        "max_segments": settings.max_segments,
        "audio_queue_chunks": settings.audio_queue_chunks,
        "max_audio_frame_bytes": settings.max_audio_frame_bytes,
        "asr_replay_seconds": settings.asr_replay_seconds,
        "asr_health_check_seconds": settings.asr_health_check_seconds,
        "faster_whisper_model": settings.faster_whisper_model,
        "faster_whisper_device": settings.faster_whisper_device,
        "faster_whisper_compute_type": settings.faster_whisper_compute_type,
        "faster_whisper_chunk_seconds": settings.faster_whisper_chunk_seconds,
        "vad_threshold_dbfs": settings.vad_threshold_dbfs,
        "vad_hangover_frames": settings.vad_hangover_frames,
        "mdns_enabled": settings.mdns_enabled,
        "audience_join_max_failures": settings.audience_join_max_failures,
        "audience_join_window_seconds": settings.audience_join_window_seconds,
        "audience_join_block_seconds": settings.audience_join_block_seconds,
    }


def build_diagnostics_bundle(
    *,
    version: str,
    settings: Settings,
    state: SessionState,
    metrics: RealtimeMetrics,
    preflight: Any | None = None,
    mdns_state: Any | None = None,
) -> bytes:
    """Create a privacy-minimized support ZIP.

    The bundle intentionally excludes transcript text, join codes, session title,
    presenter, glossary terms, hotwords, reference-document text, raw audio, API
    keys, and environment variables.
    """

    generated_at = datetime.now(UTC)
    manifest = {
        "schema_version": _DIAGNOSTICS_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "app": "LangTextFlow",
        "version": version,
        "privacy": {
            "contains_transcript_text": False,
            "contains_join_code": False,
            "contains_reference_text": False,
            "contains_glossary_terms": False,
            "contains_api_keys": False,
            "contains_user_home_path": False,
        },
    }
    system = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "cpu_count": os.cpu_count(),
        "executable": sys.executable,
    }

    entries: dict[str, Any] = {
        "manifest.json": manifest,
        "system.json": system,
        "settings.json": _safe_settings(settings),
        "session-state.json": _safe_session_state(state),
        "metrics.json": metrics.model_dump(mode="json"),
    }
    if preflight is not None:
        entries["preflight.json"] = _model_dump(preflight)
    if mdns_state is not None:
        entries["mdns.json"] = _model_dump(mdns_state)

    secrets = tuple(
        value
        for value in (settings.openai_compatible_api_key,)
        if isinstance(value, str) and value
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, value in entries.items():
            safe_value = _sanitize_diagnostics(value, secrets=secrets)
            archive.writestr(filename, _json_bytes(safe_value))
    return output.getvalue()
