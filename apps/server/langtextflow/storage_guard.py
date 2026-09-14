from __future__ import annotations

import errno
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RESERVE_BYTES = 512 * 1024 * 1024


class StorageCapacityError(RuntimeError):
    """Raised before a write that would leave the application without safe headroom."""


@dataclass(frozen=True, slots=True)
class StorageCapacity:
    path: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int
    required_bytes: int
    reserve_bytes: int

    @property
    def available_for_operation(self) -> int:
        return max(0, self.free_bytes - self.reserve_bytes)

    @property
    def sufficient(self) -> bool:
        return self.available_for_operation >= self.required_bytes


def _existing_probe_path(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def storage_capacity(
    path: str | Path,
    *,
    required_bytes: int = 0,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> StorageCapacity:
    required = max(0, int(required_bytes))
    reserve = max(0, int(reserve_bytes))
    probe = _existing_probe_path(Path(path))
    usage = shutil.disk_usage(probe)
    return StorageCapacity(
        path=probe,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        required_bytes=required,
        reserve_bytes=reserve,
    )


def ensure_storage_capacity(
    path: str | Path,
    *,
    required_bytes: int = 0,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
    operation: str = "write data",
) -> StorageCapacity:
    capacity = storage_capacity(
        path,
        required_bytes=required_bytes,
        reserve_bytes=reserve_bytes,
    )
    if capacity.sufficient:
        return capacity
    needed = capacity.required_bytes + capacity.reserve_bytes
    raise StorageCapacityError(
        f"Not enough disk space to {operation}: "
        f"{capacity.free_bytes} bytes free, {needed} bytes required including safety reserve."
    )


def is_storage_exhaustion_error(exc: BaseException) -> bool:
    """Recognize ENOSPC/EDQUOT and SQLite's disk-full variants across platforms."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in {errno.ENOSPC, errno.EDQUOT}:
            return True
        if isinstance(current, sqlite3.Error):
            message = str(current).casefold()
            if "database or disk is full" in message or "disk i/o error" in message:
                return True
        message = str(current).casefold()
        if "no space left on device" in message or "disk quota exceeded" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def storage_error_message(operation: str) -> str:
    return (
        f"{operation} failed because storage is full or unavailable. "
        "Free disk space and retry; existing session data has been left intact where possible."
    )
