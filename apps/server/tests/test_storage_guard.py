import errno
import sqlite3

import pytest

from langtextflow.storage_guard import (
    StorageCapacityError,
    ensure_storage_capacity,
    is_storage_exhaustion_error,
    storage_capacity,
)


def test_storage_capacity_uses_nearest_existing_parent(tmp_path, monkeypatch) -> None:
    target = tmp_path / "nested" / "cache" / "model.bin"

    class Usage:
        total = 10_000
        used = 2_000
        free = 8_000

    seen = []
    monkeypatch.setattr(
        "langtextflow.storage_guard.shutil.disk_usage",
        lambda path: seen.append(path) or Usage(),
    )

    capacity = storage_capacity(target, required_bytes=1_000, reserve_bytes=2_000)

    assert seen == [tmp_path]
    assert capacity.available_for_operation == 6_000
    assert capacity.sufficient is True


def test_ensure_storage_capacity_rejects_operation_without_reserve(tmp_path, monkeypatch) -> None:
    class Usage:
        total = 10_000
        used = 7_000
        free = 3_000

    monkeypatch.setattr("langtextflow.storage_guard.shutil.disk_usage", lambda _path: Usage())

    with pytest.raises(StorageCapacityError, match="Not enough disk space"):
        ensure_storage_capacity(
            tmp_path,
            required_bytes=2_500,
            reserve_bytes=1_000,
            operation="download a model",
        )


def test_storage_exhaustion_error_recognizes_os_and_sqlite_failures() -> None:
    assert is_storage_exhaustion_error(OSError(errno.ENOSPC, "no space left on device"))
    assert is_storage_exhaustion_error(OSError(errno.EDQUOT, "disk quota exceeded"))
    assert is_storage_exhaustion_error(sqlite3.OperationalError("database or disk is full"))
    assert not is_storage_exhaustion_error(sqlite3.OperationalError("database is locked"))


def test_storage_exhaustion_error_follows_exception_chain() -> None:
    inner = OSError(errno.ENOSPC, "no space left on device")
    outer = RuntimeError("download failed")
    outer.__cause__ = inner

    assert is_storage_exhaustion_error(outer)
