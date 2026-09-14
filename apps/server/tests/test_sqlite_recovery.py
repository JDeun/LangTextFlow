import sqlite3

from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.sqlite_recovery import recover_sqlite_if_corrupt


def test_healthy_database_is_left_untouched(tmp_path) -> None:
    database = tmp_path / "healthy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")

    before = database.read_bytes()
    result = recover_sqlite_if_corrupt(database)

    assert result.recovered is False
    assert result.quarantined_path is None
    assert database.read_bytes() == before


def test_corrupt_database_is_quarantined_and_can_be_rebuilt(tmp_path) -> None:
    database = tmp_path / "langtextflow.db"
    corrupt_bytes = b"not-a-sqlite-database\x00private-history"
    database.write_bytes(corrupt_bytes)

    result = recover_sqlite_if_corrupt(database)

    assert result.recovered is True
    assert result.quarantined_path is not None
    assert result.quarantined_path.exists()
    assert result.quarantined_path.read_bytes() == corrupt_bytes
    assert not database.exists()
    assert result.reason

    repository = GlossaryRepository(str(database))
    repository.initialize()
    assert database.exists()
    assert repository.list() == []


def test_corrupt_database_companion_files_are_quarantined(tmp_path) -> None:
    database = tmp_path / "langtextflow.db"
    database.write_bytes(b"broken")
    wal = tmp_path / "langtextflow.db-wal"
    shm = tmp_path / "langtextflow.db-shm"
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")

    result = recover_sqlite_if_corrupt(database)

    assert result.quarantined_path is not None
    assert result.quarantined_path.exists()
    assert result.quarantined_path.with_name(result.quarantined_path.name + "-wal").read_bytes() == b"wal"
    assert result.quarantined_path.with_name(result.quarantined_path.name + "-shm").read_bytes() == b"shm"
    assert not wal.exists()
    assert not shm.exists()
