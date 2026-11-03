"""One immutable model/tokenizer generation, selected by an atomic pointer."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .config import Settings

_GENERATION = re.compile(r"[0-9a-f]{32}")


def managed_m2m100_root(settings: Settings) -> Path:
    return Path(settings.model_cache_dir).expanduser().resolve() / "m2m100-native"


def uses_managed_m2m100(settings: Settings) -> bool:
    # Explicit custom paths retain their existing offline/manual contract.
    return (settings.local_m2m100_ct2_dir == "data/model-cache/m2m100-ct2"
            and settings.local_m2m100_tokenizer_dir == "data/model-cache/m2m100-tokenizer")


def m2m100_cache_paths(settings: Settings) -> tuple[Path, Path]:
    if not uses_managed_m2m100(settings):
        return Path(settings.local_m2m100_ct2_dir), Path(settings.local_m2m100_tokenizer_dir)
    root = managed_m2m100_root(settings)
    pointer = root / "current.json"
    if pointer.exists() or pointer.is_symlink():
        if root.is_symlink() or pointer.is_symlink() or pointer.stat().st_size > 128:
            raise ValueError("invalid managed M2M100 cache pointer")
        generation = json.loads(pointer.read_bytes())
        if not isinstance(generation, str) or not _GENERATION.fullmatch(generation):
            raise ValueError("invalid managed M2M100 cache generation")
        directory = root / generation
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("missing managed M2M100 cache generation")
        return directory / "model", directory / "tokenizer"
    return Path(settings.local_m2m100_ct2_dir), Path(settings.local_m2m100_tokenizer_dir)


def publish_m2m100_cache(root: Path, generation: str) -> None:
    if not _GENERATION.fullmatch(generation) or root.is_symlink():
        raise ValueError("invalid managed M2M100 cache generation")
    temporary = root / f".{generation}.json"
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(generation, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, root / "current.json")
    finally:
        temporary.unlink(missing_ok=True)
