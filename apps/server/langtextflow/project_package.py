"""Private project ZIP transport and durable ownership, never executable SQL input."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import struct
import threading
import time
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from .post_event_archive import _now
from .project_package_graph import (
    COLUMNS,
    EXPORT_SQL,
    FORMAT,
    INSERT_SQL,
    MAX_GRAPH_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_MEDIA,
    MAX_PACKAGE_BYTES,
    MAX_ROWS,
    PRIMARY_KEYS,
    VERSION,
    PackageError,
    canonical,
    parse_json,
    validate_graph,
)
from .recording_journal import RecoveryStopped
from .storage_guard import ensure_storage_capacity

BLOCK = 1024 * 1024
_NAME = re.compile(r"[0-9a-f]{32}\.(?:wav|zip)")


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _sync_directory(path):
    if os.name == "nt":
        return  # Windows file handles are flushed; POSIX directory FDs do not apply.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _regular(path):
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise PackageError("Private package asset is not a regular file")
    return info


def _open_read(path):
    before = _regular(path)
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    if _identity(os.fstat(stream.fileno())) != _identity(before):
        stream.close()
        raise PackageError("Private package asset changed")
    return stream


def _hash(stream, stop=None):
    result = hashlib.sha256()
    stream.seek(0)
    while block := stream.read(BLOCK):
        if stop is not None and stop.is_set():
            raise RecoveryStopped
        result.update(block)
    return result.hexdigest()


class ProjectPackageStore:
    def __init__(self, archive):
        self.archive = archive
        self.root = archive.database_path.parent / "project-packages"
        self.active = set()
        self.active_lock = threading.Lock()

    def initialize(self):
        if self.root.is_symlink():
            raise PackageError("Private package directory must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.archive._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS project_package_owners (
                    owner_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('import','export')),
                    project_id TEXT,
                    state TEXT NOT NULL CHECK(state IN ('prepared','committed','quarantined')),
                    receipt_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_package_files (
                    owner_id TEXT NOT NULL
                        REFERENCES project_package_owners(owner_id) ON DELETE CASCADE,
                    area TEXT NOT NULL CHECK(area IN ('recordings','packages')),
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(owner_id,area,filename)
                );
                CREATE INDEX IF NOT EXISTS idx_project_package_file
                    ON project_package_files(area,filename);
            """)

        self.cleanup_partials()

    def reserve(self, identifier, kind, project_id, filename, size, sha):
        self._check_file("packages", filename, size, sha)
        with self.archive._connect() as conn:
            conn.execute(
                "INSERT INTO project_package_owners VALUES (?,?,?,'prepared',NULL,NULL,?)",
                (identifier, kind, project_id, _now()),
            )
            conn.execute(
                "INSERT INTO project_package_files VALUES (?,'packages',?,?,?,'{}')",
                (identifier, filename, size, sha),
            )

    def cleanup_partials(self):
        # A crash before an asset was complete has no ownership receipt. Only
        # old service-generated regular ZIP partials are eligible; never WAVs.
        with self.archive._connect() as conn, os.scandir(self.root) as entries:
            for index, entry in enumerate(entries):
                if index >= 256:
                    break
                if not re.fullmatch(r"[0-9a-f]{32}\.zip\.partial", entry.name):
                    continue
                if conn.execute(
                    "SELECT 1 FROM project_package_files WHERE area='packages' AND filename=?",
                    (entry.name.removesuffix(".partial"),),
                ).fetchone():
                    continue
                if (
                    entry.is_file(follow_symlinks=False)
                    and entry.stat().st_mtime < time.time() - 86400
                ):
                    with suppress(OSError):
                        os.unlink(entry.path)

    def own_file(self, identifier, area, filename, size, sha, metadata):
        self._check_file(area, filename, size, sha)
        with self.archive._connect() as conn:
            conn.execute(
                "INSERT INTO project_package_files VALUES (?,?,?,?,?,?)",
                (identifier, area, filename, size, sha, canonical(metadata)),
            )

    @staticmethod
    def _check_file(area, filename, size, sha):
        extension = {"packages": ".zip", "recordings": ".wav"}.get(area)
        if (
            extension is None
            or not isinstance(filename, str)
            or not _NAME.fullmatch(filename)
            or not filename.endswith(extension)
            or type(size) is not int
            or not 0 < size <= MAX_PACKAGE_BYTES
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
        ):
            raise PackageError("Invalid private package ownership")

    def state(self, identifier):
        with self.archive._connect() as conn:
            row = conn.execute(
                "SELECT state FROM project_package_owners WHERE owner_id=?", (identifier,)
            ).fetchone()
        return row[0] if row else None

    def receipts(self):
        with self.archive._connect() as conn:
            rows = conn.execute(
                "SELECT receipt_json FROM project_package_owners "
                "WHERE state='committed' ORDER BY rowid DESC LIMIT 20"
            ).fetchall()
        return [parse_json(row[0]) for row in rows]

    def repairs(self):
        with self.archive._connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT owner_id,created_at,error FROM project_package_owners "
                    "WHERE state='quarantined' ORDER BY rowid DESC LIMIT 20",
                )
            ]

    def snapshot(self, project_id, stop):
        tables, assets = {}, {}
        total = encoded_size = 0
        with self.archive._connect() as conn:
            conn.execute("BEGIN")
            for table, query in EXPORT_SQL.items():
                rows = []
                for result in conn.execute(query, (project_id, MAX_ROWS + 1)):
                    if stop.is_set():
                        raise RecoveryStopped
                    row = dict(result)
                    total += 1
                    if total > MAX_ROWS:
                        raise PackageError("Project exceeds the 50000-row backup limit", 413)
                    if table == "session_recordings":
                        assets[row["recording_id"]] = dict(row)
                        row.pop("filename")
                    encoded_size += len(canonical(row).encode()) + 1
                    if encoded_size > MAX_GRAPH_BYTES - 4096:
                        raise PackageError("Project graph exceeds the 32 MiB backup limit", 413)
                    rows.append(row)
                tables[table] = rows
            if not tables["projects"]:
                raise PackageError("Project not found", 404)
            pending = conn.execute(
                "SELECT 1 FROM recording_journal r JOIN project_sessions p "
                "ON p.session_id=r.session_id WHERE p.project_id=? AND r.registered=0 LIMIT 1",
                (project_id,),
            ).fetchone()
            if pending:
                raise PackageError("Finish or repair pending recording recovery before backup", 409)
            graph = validate_graph({"project_id": project_id, "tables": tables})
        data = canonical(graph).encode()
        if len(data) > MAX_GRAPH_BYTES:
            raise PackageError("Project graph exceeds the 32 MiB backup limit", 413)
        if len(assets) > MAX_MEDIA:
            raise PackageError("Project exceeds 64 recording assets", 413)
        size = sum(row["size_bytes"] for row in assets.values()) + len(data) + MAX_MANIFEST_BYTES
        if size > MAX_PACKAGE_BYTES:
            raise PackageError("Complete project package exceeds 1 GiB; reduce its scope", 413)
        return graph, data, assets

    def scope_present(self, project_id, session_ids):
        with self.archive._connect() as conn:
            conn.execute("BEGIN")
            if not conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
            ).fetchone():
                return False
            current = {
                row[0]
                for row in conn.execute(
                    "SELECT session_id FROM project_sessions WHERE project_id=?",
                    (project_id,),
                )
            }
            return current == set(session_ids)

    def _paths(self, row):
        self._check_file(row["area"], row["filename"], row["size_bytes"], row["sha256"])
        root = self.root if row["area"] == "packages" else self.archive.audio_root
        if root.is_symlink():
            raise PackageError("Private asset root must not be a symlink")
        return root / row["filename"], root / (row["filename"] + ".partial")

    def clean_owner(self, identifier, stop=None):
        with self.archive._connect() as conn:
            owner = conn.execute(
                "SELECT * FROM project_package_owners WHERE owner_id=?", (identifier,)
            ).fetchone()
            files = conn.execute(
                "SELECT * FROM project_package_files WHERE owner_id=?", (identifier,)
            ).fetchall()
        if owner is None:
            return
        for row in files:
            if stop is not None and stop.is_set():
                raise RecoveryStopped
            if row["area"] == "recordings":
                with self.archive._connect() as conn:
                    registered = conn.execute(
                        "SELECT 1 FROM session_recordings WHERE filename=?", (row["filename"],)
                    ).fetchone()
                if registered:
                    raise PackageError("A committed recording cannot be removed by package cleanup")
            for path in self._paths(row):
                if not path.exists() and not path.is_symlink():
                    continue
                before = _regular(path)
                if before.st_size != row["size_bytes"]:
                    raise PackageError("Private package cleanup size changed")
                if row["area"] == "recordings":
                    metadata = parse_json(row["metadata_json"])
                    self.archive.recording_journal._check_metadata(metadata)
                    if any(
                        metadata[key] != row[key] for key in ("filename", "sha256", "size_bytes")
                    ):
                        raise PackageError("Recording ownership metadata changed")
                    identity = self.archive.recording_journal._verify(
                        metadata,
                        path,
                        stop,
                    )
                else:
                    with _open_read(path) as stream:
                        if _hash(stream, stop) != row["sha256"]:
                            raise PackageError("Private package cleanup hash changed")
                        identity = _identity(os.fstat(stream.fileno()))
                # Recheck durable ownership in a short write transaction after
                # hashing outside SQLite. A concurrent commit must win safely.
                with self.archive._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if not conn.execute(
                        "SELECT 1 FROM project_package_files "
                        "WHERE owner_id=? AND area=? AND filename=?",
                        (identifier, row["area"], row["filename"]),
                    ).fetchone():
                        continue
                    if (
                        row["area"] == "recordings"
                        and conn.execute(
                            "SELECT 1 FROM session_recordings WHERE filename=?",
                            (row["filename"],),
                        ).fetchone()
                    ):
                        raise PackageError("A committed recording cannot be removed")
                    if _identity(_regular(path)) != identity:
                        raise PackageError("Private package cleanup entry changed")
                    path.unlink()
                    _sync_directory(path.parent)
            _sync_directory(self._paths(row)[0].parent)
            with self.archive._connect() as conn:
                conn.execute(
                    "DELETE FROM project_package_files WHERE owner_id=? AND area=? AND filename=?",
                    (identifier, row["area"], row["filename"]),
                )
        with self.archive._connect() as conn:
            conn.execute(
                "DELETE FROM project_package_owners WHERE owner_id=? AND state!='committed'",
                (identifier,),
            )

    def recover_one(self, stop):
        with self.archive._connect() as conn:
            rows = conn.execute(
                "SELECT owner_id FROM project_package_owners WHERE state!='quarantined' "
                "AND EXISTS(SELECT 1 FROM project_package_files f "
                "WHERE f.owner_id=project_package_owners.owner_id) ORDER BY rowid LIMIT 2",
            ).fetchall()
        with self.active_lock:
            row = next((row for row in rows if row[0] not in self.active), None)
        if row is None:
            return 0
        try:
            self.clean_owner(row[0], stop)
        except RecoveryStopped:
            raise
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            with self.archive._connect() as conn:
                conn.execute(
                    "UPDATE project_package_owners SET state='quarantined',error=? "
                    "WHERE owner_id=?",
                    ("Private package integrity check requires repair", row[0]),
                )
        return 1


class ProjectPackageOperation:
    """One owned native I/O operation; cancellation never races its file handles."""

    def __init__(self, store, kind, reserve_bytes, project_id=None):
        self.store, self.kind, self.reserve_bytes = store, kind, reserve_bytes
        self.project_id = project_id
        self.identifier = uuid4().hex
        self.zip_name = f"{uuid4().hex}.zip"
        self.zip_path = store.root / (self.zip_name + ".partial")
        self.stop, self.done = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.stream = None
        self.size = 0
        self.sha = hashlib.sha256()
        self.created = {}
        self.prepared = None
        self.reserved = False
        self.session_ids = set()
        self.created_at = datetime.now(UTC)
        with store.active_lock:
            store.active.add(self.identifier)

    def call(self, method, *args):
        with self.lock:
            try:
                self.check()
                return method(*args)
            except BaseException:
                self._abort_locked()
                raise
            finally:
                if self.stop.is_set():
                    self._abort_locked()

    def check(self):
        if self.stop.is_set():
            raise RecoveryStopped

    def capacity(self, size):
        ensure_storage_capacity(
            self.store.root,
            required_bytes=size,
            reserve_bytes=self.reserve_bytes,
            operation="prepare a private project package",
        )

    def _new_file(self, path):
        if path.parent.is_symlink():
            raise PackageError("Private file root must not be a symlink")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        info = os.fstat(fd)
        self.created[path] = info.st_dev, info.st_ino
        try:
            return os.fdopen(fd, "w+b")
        except BaseException:
            os.close(fd)
            raise

    def open_upload(self):
        self.capacity(BLOCK)
        self.stream = self._new_file(self.zip_path)
        self.check()

    def write(self, data):
        if self.size + len(data) > MAX_PACKAGE_BYTES:
            raise PackageError("Private package exceeds 1 GiB", 413)
        self.capacity(len(data))
        self.stream.write(data)
        self.sha.update(data)
        self.size += len(data)
        self.check()

    def _reserve_zip(self):
        if self.size < 22:
            raise PackageError("Private package ZIP is truncated")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        _sync_directory(self.zip_path.parent)
        if os.fstat(self.stream.fileno()).st_size != self.size:
            raise PackageError("Private package upload size changed")
        self.reserved = True
        self.store.reserve(
            self.identifier,
            self.kind,
            self.project_id,
            self.zip_name,
            self.size,
            self.sha.hexdigest(),
        )

    @staticmethod
    def _entry(name, size):
        info = zipfile.ZipInfo(name)
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        info.file_size = size
        return info

    def export(self, snapshot):
        graph, data, assets = snapshot
        self.session_ids = {row["session_id"] for row in graph["tables"]["sessions"]}
        self.capacity(
            sum(row["size_bytes"] for row in assets.values()) + len(data) + MAX_MANIFEST_BYTES
        )
        self.stream = self._new_file(self.zip_path)
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": _now(),
            "graph_sha256": hashlib.sha256(data).hexdigest(),
            "graph_size_bytes": len(data),
            "counts": {table: len(rows) for table, rows in graph["tables"].items()},
            "media": [],
        }
        with zipfile.ZipFile(
            self.stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False
        ) as output:
            output.writestr(self._entry("graph.json", len(data)), data)
            for rid, row in assets.items():
                self.check()
                path = self.store.archive.recording_path(row["session_id"], rid)
                if path is None:
                    raise PackageError(
                        "A recording is missing; repair the project before backup", 409
                    )
                identity = self.store.archive.recording_journal._verify(row, path, self.stop)
                entry = f"media/{rid}.wav"
                digest = hashlib.sha256()
                copied = 0
                with (
                    _open_read(path) as source,
                    output.open(self._entry(entry, row["size_bytes"]), "w") as target,
                ):
                    if _identity(os.fstat(source.fileno())) != identity:
                        raise PackageError("Recording changed during backup", 409)
                    while block := source.read(BLOCK):
                        self.check()
                        copied += len(block)
                        if copied > row["size_bytes"]:
                            raise PackageError("Recording grew during backup", 409)
                        target.write(block)
                        digest.update(block)
                    if (
                        copied != row["size_bytes"]
                        or digest.hexdigest() != row["sha256"]
                        or _identity(os.fstat(source.fileno())) != identity
                    ):
                        raise PackageError("Recording hash changed during backup", 409)
                manifest["media"].append(
                    {
                        "recording_id": rid,
                        "entry": entry,
                        "size_bytes": copied,
                        "sha256": digest.hexdigest(),
                    }
                )
            encoded = canonical(manifest).encode()
            if len(encoded) > MAX_MANIFEST_BYTES:
                raise PackageError("Private package manifest exceeds its limit", 413)
            output.writestr(self._entry("manifest.json", len(encoded)), encoded)
        self.size = self.stream.tell()
        if self.size > MAX_PACKAGE_BYTES:
            raise PackageError("Complete project package exceeds 1 GiB", 413)
        self.sha = hashlib.sha256()
        self.stream.seek(0)
        while block := self.stream.read(BLOCK):
            self.check()
            self.sha.update(block)
        self._reserve_zip()
        self.stream.close()
        self.stream = None
        self.zip_identity = _identity(_regular(self.zip_path))
        if not self.store.scope_present(self.project_id, self.session_ids):
            raise PackageError("Project membership changed during backup", 409)
        self.prepared = {
            "export_id": self.identifier,
            "project_id": self.project_id,
            "size_bytes": self.size,
            "sha256": self.sha.hexdigest(),
        }
        return self.prepared

    def _archive(self):
        self.stream.seek(-22, os.SEEK_END)
        raw = self.stream.read(22)
        if len(raw) != 22:
            raise PackageError("Private package ZIP is truncated")
        signature, disk, central_disk, entries_disk, entries, central_size, offset, comment = (
            struct.unpack("<4s4H2LH", raw)
        )
        if (
            signature != b"PK\x05\x06"
            or disk
            or central_disk
            or comment
            or entries_disk != entries
            or not 2 <= entries <= MAX_MEDIA + 2
            or central_size > 512 * 1024
            or offset + central_size != self.size - 22
        ):
            raise PackageError("Unsupported or oversized private ZIP directory")
        archive = zipfile.ZipFile(self.stream, "r", allowZip64=False)
        infos = archive.infolist()
        if len(infos) != entries or len({info.filename.casefold() for info in infos}) != entries:
            archive.close()
            raise PackageError("Duplicate private ZIP entries")
        total = 0
        for info in infos:
            total += info.file_size
            if (
                info.compress_type != zipfile.ZIP_STORED
                or info.compress_size != info.file_size
                or info.flag_bits & 1
                or info.extra
                or info.extract_version > 20
                or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG)
                or info.file_size < 0
                or total > MAX_PACKAGE_BYTES
                or info.header_offset < 0
                or info.header_offset >= offset
            ):
                archive.close()
                raise PackageError("Unsupported private ZIP entry or expansion ratio")
        return archive, {info.filename: info for info in infos}

    def _read_entry(self, archive, info, limit):
        if info.file_size > limit:
            raise PackageError("Private package metadata exceeds its limit", 413)
        with archive.open(info) as source:
            result = bytearray()
            while block := source.read(min(BLOCK, limit + 1 - len(result))):
                self.check()
                result.extend(block)
                if len(result) > limit:
                    raise PackageError("Private package metadata exceeds its limit", 413)
        if len(result) != info.file_size:
            raise PackageError("Private package entry size changed")
        return bytes(result)

    def restore(self):
        self._reserve_zip()
        archive, infos = self._archive()
        with archive:
            if "graph.json" not in infos or "manifest.json" not in infos:
                raise PackageError("Private project metadata entries are missing")
            manifest = parse_json(
                self._read_entry(archive, infos["manifest.json"], MAX_MANIFEST_BYTES)
            )
            raw_graph = self._read_entry(archive, infos["graph.json"], MAX_GRAPH_BYTES)
            if (
                not isinstance(manifest, dict)
                or set(manifest)
                != {
                    "format",
                    "version",
                    "created_at",
                    "graph_sha256",
                    "graph_size_bytes",
                    "counts",
                    "media",
                }
                or manifest["format"] != FORMAT
                or manifest["version"] != VERSION
                or manifest["graph_size_bytes"] != len(raw_graph)
                or manifest["graph_sha256"] != hashlib.sha256(raw_graph).hexdigest()
            ):
                raise PackageError("Private project manifest or graph hash is invalid")
            graph = validate_graph(parse_json(raw_graph))
            self.project_id = graph["project_id"]
            self.session_ids = {row["session_id"] for row in graph["tables"]["sessions"]}
            rows = graph["tables"]["session_recordings"]
            expected = {row["recording_id"]: row for row in rows}
            if (
                manifest["counts"] != {name: len(data) for name, data in graph["tables"].items()}
                or not isinstance(manifest["media"], list)
                or len(manifest["media"]) != len(rows)
            ):
                raise PackageError("Private package cumulative counts changed")
            media = {}
            for item in manifest["media"]:
                if not isinstance(item, dict) or set(item) != {
                    "recording_id",
                    "entry",
                    "size_bytes",
                    "sha256",
                }:
                    raise PackageError("Invalid private media manifest")
                rid = item["recording_id"]
                row = expected.get(rid)
                if (
                    row is None
                    or rid in media
                    or item["entry"] != f"media/{rid}.wav"
                    or item["size_bytes"] != row["size_bytes"]
                    or item["sha256"] != row["sha256"]
                ):
                    raise PackageError("Media manifest reference leaves this package")
                media[rid] = item
            if set(infos) != {
                "graph.json",
                "manifest.json",
                *(item["entry"] for item in media.values()),
            }:
                raise PackageError("Unexpected private ZIP paths or missing media")
            self.capacity(sum(row["size_bytes"] for row in rows) + len(raw_graph) * 2)
            self._check_collisions(graph)
            staged = {}
            total = 0
            for rid, item in media.items():
                self.check()
                info = infos[item["entry"]]
                row = {**expected[rid], "filename": f"{uuid4().hex}.wav"}
                if info.file_size != row["size_bytes"]:
                    raise PackageError("Recording entry size does not match the manifest")
                path = self.store.archive.audio_root / (row["filename"] + ".partial")
                digest = hashlib.sha256()
                copied = 0
                with archive.open(info) as source, self._new_file(path) as target:
                    while block := source.read(BLOCK):
                        self.check()
                        copied += len(block)
                        total += len(block)
                        if copied > row["size_bytes"] or total > MAX_PACKAGE_BYTES:
                            raise PackageError(
                                "Private recording bytes exceed the package limits", 413
                            )
                        target.write(block)
                        digest.update(block)
                    target.flush()
                    os.fsync(target.fileno())
                _sync_directory(path.parent)
                if copied != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                    raise PackageError("Recording checksum mismatch")
                identity = self.store.archive.recording_journal._verify(row, path, self.stop)
                self.store.own_file(
                    self.identifier, "recordings", row["filename"], copied, row["sha256"], row
                )
                staged[rid] = row, identity
        self.check()
        self._commit(graph, staged)
        return {
            "import_id": self.identifier,
            "project_id": self.project_id,
            "sessions": len(graph["tables"]["sessions"]),
            "recordings": len(staged),
        }

    def _check_collisions(self, graph, *, connection=None):
        if connection is None:
            with self.store.archive._connect() as conn:
                return self._check_collisions(graph, connection=conn)
        for table, rows in graph["tables"].items():
            keys = PRIMARY_KEYS[table]
            for row in rows:
                self.check()
                if connection.execute(
                    CONFLICT_SQL[table], tuple(row[key] for key in keys)
                ).fetchone():
                    raise PackageError(
                        "A project or historical ID already exists; use an empty data profile", 409
                    )
        # Deleted-session ownership tombstones are still live private identities.
        for row in graph["tables"]["session_recordings"]:
            if connection.execute(
                "SELECT 1 FROM recording_journal WHERE recording_id=?", (row["recording_id"],)
            ).fetchone():
                raise PackageError("A historical recording identity already exists", 409)

    def _promote(self, row, identity):
        path = self.store.archive.audio_root / (row["filename"] + ".partial")
        final = self.store.archive.audio_root / row["filename"]
        if _identity(_regular(path)) != identity or final.exists() or final.is_symlink():
            raise PackageError("Prepared recording ownership changed")
        if os.name == "nt":
            os.rename(path, final)  # Windows rename refuses an existing target.
        else:
            os.link(path, final, follow_symlinks=False)  # POSIX no-replace promotion.
            path.unlink()
        self.created[final] = self.created.pop(path)
        if _identity(_regular(final)) != identity:
            raise PackageError("Promoted recording ownership changed")

    def _commit(self, graph, staged):
        receipt = {
            "import_id": self.identifier,
            "project_id": self.project_id,
            "sessions": len(graph["tables"]["sessions"]),
            "recordings": len(staged),
            "created_at": _now(),
        }
        with self.store.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._check_collisions(graph, connection=conn)
            self.check()
            for row, identity in staged.values():
                self._promote(row, identity)
            if staged:
                _sync_directory(self.store.archive.audio_root)
            for table in COLUMNS:
                rows = graph["tables"][table]
                self.check()
                native_rows = (
                    [staged[row["recording_id"]][0] for row in rows]
                    if table == "session_recordings"
                    else rows
                )
                conn.executemany(
                    INSERT_SQL[table],
                    [tuple(row[column] for column in COLUMNS[table]) for row in native_rows],
                )
            for row, _ in staged.values():
                conn.execute(
                    "INSERT INTO recording_journal "
                    "(recording_id,session_id,owner_session_id,filename,size_bytes,sha256,"
                    "duration_seconds,sample_rate,channels,created_at,registered) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        row["recording_id"],
                        row["session_id"],
                        row["session_id"],
                        row["filename"],
                        row["size_bytes"],
                        row["sha256"],
                        row["duration_seconds"],
                        row["sample_rate"],
                        row["channels"],
                        row["created_at"],
                    ),
                )
            # foreign_keys=ON checks each insertion. No full destination scan.
            self.check()
            conn.execute(
                "DELETE FROM project_package_files WHERE owner_id=? AND area='recordings'",
                (self.identifier,),
            )
            conn.execute(
                "UPDATE project_package_owners SET state='committed',project_id=?,receipt_json=? "
                "WHERE owner_id=?",
                (self.project_id, canonical(receipt), self.identifier),
            )

    def read(self):
        if self.stream is None:
            if not self.prepared or not self.store.scope_present(self.project_id, self.session_ids):
                raise PackageError("Project scope changed before backup download", 409)
            self.stream = _open_read(self.zip_path)
            if _identity(os.fstat(self.stream.fileno())) != self.zip_identity:
                raise PackageError("Private backup size changed")
            self.stream.seek(0)
        return self.stream.read(BLOCK)

    def abort(self):
        self.stop.set()
        with self.lock:
            self._abort_locked()

    def _abort_locked(self):
        if self.stream is not None:
            with suppress(OSError):
                self.stream.close()
            self.stream = None
        try:
            state = self.store.state(self.identifier) if self.reserved else None
        except sqlite3.Error:
            with self.store.active_lock:
                self.store.active.discard(self.identifier)
            self.done.set()
            return  # Never risk removing a recording after an uncertain commit.
        synced = True
        for path, identity in self.created.items():
            if state == "committed" and path.parent == self.store.archive.audio_root:
                continue
            with suppress(OSError):
                info = path.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                    path.unlink()
                    try:
                        _sync_directory(path.parent)
                    except OSError:
                        synced = False
        if self.reserved and synced:
            with suppress(ValueError, OSError, sqlite3.Error):
                self.store.clean_owner(self.identifier)
        with self.store.active_lock:
            self.store.active.discard(self.identifier)
        self.done.set()


# Static ID-only queries never load unrelated transcripts or blobs during conflict checks.
CONFLICT_SQL = {
    "projects": "SELECT 1 FROM projects WHERE project_id=?",
    "sessions": "SELECT 1 FROM sessions WHERE session_id=?",
    "project_sessions": "SELECT 1 FROM project_sessions WHERE session_id=?",
    "transcript_segments": "SELECT 1 FROM transcript_segments WHERE session_id=? AND segment_id=?",
    "session_recordings": "SELECT 1 FROM session_recordings WHERE recording_id=?",
    "transcript_versions": "SELECT 1 FROM transcript_versions WHERE transcript_id=?",
    "transcript_reviews": "SELECT 1 FROM transcript_reviews WHERE review_id=?",
    "approved_transcripts": "SELECT 1 FROM approved_transcripts WHERE session_id=?",
    "session_summaries": "SELECT 1 FROM session_summaries WHERE summary_id=?",
    "summary_reviews": "SELECT 1 FROM summary_reviews WHERE review_id=?",
    "project_glossary_entries": "SELECT 1 FROM project_glossary_entries WHERE id=?",
    "project_digests": "SELECT 1 FROM project_digests WHERE digest_id=?",
    "project_digest_reviews": "SELECT 1 FROM project_digest_reviews WHERE review_id=?",
    "project_digest_sessions": (
        "SELECT 1 FROM project_digest_sessions WHERE digest_id=? AND session_id=?"
    ),
}
