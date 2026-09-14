from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class SqliteRecoveryResult:
    recovered: bool
    quarantined_path: Path | None = None
    reason: str | None = None


def recover_sqlite_if_corrupt(database_path: str | Path) -> SqliteRecoveryResult:
    """Quarantine a corrupt SQLite database so repositories can rebuild cleanly.

    A healthy or not-yet-created database is left untouched. When SQLite cannot
    open/check the database, the main file plus WAL/SHM companions are renamed
    with one timestamp. No destructive salvage is attempted automatically: the
    quarantined files remain available for manual recovery/support.
    """

    path = Path(database_path).expanduser().resolve()
    if not path.exists():
        return SqliteRecoveryResult(recovered=False)

    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
        if row and str(row[0]).casefold() == "ok":
            return SqliteRecoveryResult(recovered=False)
        reason = str(row[0])[:500] if row else "quick_check returned no result"
    except sqlite3.DatabaseError as exc:
        reason = str(exc)[:500]

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    quarantined = path.with_name(f"{path.name}.corrupt-{stamp}")
    path.parent.mkdir(parents=True, exist_ok=True)

    companions = (
        (path, ""),
        (Path(f"{path}-wal"), "-wal"),
        (Path(f"{path}-shm"), "-shm"),
    )
    for source, suffix in companions:
        if source.exists():
            os.replace(source, Path(f"{quarantined}{suffix}"))

    return SqliteRecoveryResult(
        recovered=True,
        quarantined_path=quarantined,
        reason=reason,
    )
