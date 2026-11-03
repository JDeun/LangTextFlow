"""Shared local Whisper lookup; downloads belong only to explicit preparation."""
from __future__ import annotations

import os
from pathlib import Path

WHISPER_REPOSITORIES = {
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
# Official legacy conversions may omit preprocessor_config.json; native defaults are local.
WHISPER_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json")


def managed_whisper_cache(model_cache_dir: str | Path) -> Path:
    return Path(model_cache_dir).expanduser().resolve() / "huggingface"


def _complete_model(path: str | Path) -> Path:
    path = Path(path).expanduser().resolve()
    missing = [name for name in WHISPER_REQUIRED_FILES if not (path / name).is_file()]
    if missing:
        raise FileNotFoundError("Whisper snapshot is incomplete: " + ", ".join(missing))
    return path


def resolve_whisper_model(model: str, cache_dir: str | Path | None = None) -> Path:
    """Read managed, configured and legacy caches without downloads or migration."""
    local = Path(model).expanduser()
    if local.is_dir():
        return _complete_model(local)
    repo = model if "/" in model else WHISPER_REPOSITORIES.get(model)
    if repo is None:
        raise ValueError(f"unsupported faster-whisper model: {model}")

    from huggingface_hub import constants, snapshot_download

    roots: list[Path] = []
    if cache_dir is not None:
        managed = Path(cache_dir).expanduser()
        roots.extend([managed, managed / "hub"])
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if value := os.environ.get(name):
            roots.append(Path(os.path.expandvars(value)).expanduser())
    if home := os.environ.get("HF_HOME"):
        configured = Path(os.path.expandvars(home)).expanduser()
        roots.extend([configured / "hub", configured])
    roots.append(Path(constants.HF_HUB_CACHE))
    legacy = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "huggingface"
    roots.extend([legacy / "hub", legacy])
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        try:
            return _complete_model(snapshot_download(
                repo, cache_dir=str(root), local_files_only=True,
                allow_patterns=[
                    *WHISPER_REQUIRED_FILES, "preprocessor_config.json", "vocabulary.*",
                ],
            ))
        except (OSError, ValueError):
            continue
    raise FileNotFoundError(f"Whisper model {model} is not prepared in a local cache")


def prepare_whisper_model(model: str, cache_dir: str | Path | None = None) -> Path:
    """Explicit setup/bootstrap only; reuse a complete local model before downloading."""
    try:
        return resolve_whisper_model(model, cache_dir)
    except FileNotFoundError:
        if Path(model).expanduser().is_dir():
            raise  # Never turn an incomplete explicit directory into a remote model ID.
    from faster_whisper.utils import download_model

    destination = str(Path(cache_dir).expanduser().resolve()) if cache_dir is not None else None
    return _complete_model(download_model(model, cache_dir=destination))
