from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Settings
from .storage_guard import storage_capacity

_ALLOWED_CACHE_AREAS = {"huggingface", "vibevoice"}


class CacheEntry(BaseModel):
    area: str
    path: str
    bytes: int = Field(ge=0)
    files: int = Field(ge=0)
    modified_at: datetime | None = None


class CacheInventory(BaseModel):
    root: str
    total_bytes: int = Field(ge=0)
    entries: list[CacheEntry]
    filesystem_free_bytes: int = Field(default=0, ge=0)
    safety_reserve_bytes: int = Field(default=0, ge=0)
    writable_bytes: int = Field(default=0, ge=0)


class ModelCacheManager:
    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.model_cache_dir).expanduser().resolve()
        self.reserve_bytes = settings.storage_reserve_mb * 1024 * 1024

    async def inventory(self) -> CacheInventory:
        return await asyncio.to_thread(self._inventory_sync)

    async def clear(self, area: str) -> CacheInventory:
        if area not in _ALLOWED_CACHE_AREAS:
            raise ValueError("unsupported cache area")
        await asyncio.to_thread(self._clear_sync, area)
        return await self.inventory()

    def _inventory_sync(self) -> CacheInventory:
        self.root.mkdir(parents=True, exist_ok=True)
        entries: list[CacheEntry] = []
        total = 0
        for area in sorted(_ALLOWED_CACHE_AREAS):
            path = self.root / area
            size, files, modified = _measure_tree(path)
            total += size
            entries.append(
                CacheEntry(
                    area=area,
                    path=str(path),
                    bytes=size,
                    files=files,
                    modified_at=modified,
                )
            )
        capacity = storage_capacity(self.root, reserve_bytes=self.reserve_bytes)
        return CacheInventory(
            root=str(self.root),
            total_bytes=total,
            entries=entries,
            filesystem_free_bytes=capacity.free_bytes,
            safety_reserve_bytes=capacity.reserve_bytes,
            writable_bytes=capacity.available_for_operation,
        )

    def _clear_sync(self, area: str) -> None:
        target = (self.root / area).resolve()
        if target.parent != self.root:
            raise ValueError("cache path escaped configured root")
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)


def _measure_tree(path: Path) -> tuple[int, int, datetime | None]:
    if not path.exists():
        return 0, 0, None
    total = 0
    files = 0
    latest: float | None = None
    for item in path.rglob("*"):
        if not item.is_file() or item.is_symlink():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        total += stat.st_size
        files += 1
        latest = max(latest or stat.st_mtime, stat.st_mtime)
    modified = datetime.fromtimestamp(latest, UTC) if latest is not None else None
    return total, files, modified
