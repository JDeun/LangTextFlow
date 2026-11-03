"""Durable ownership of prepared, registered, and deleted-session WAV assets."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sqlite3
import stat
import threading
import wave
from datetime import UTC, datetime
from uuid import uuid4

MAX_DURATION_SECONDS = 4 * 60 * 60
MAX_MEDIA_BYTES = 512 * 1024 * 1024
RECOVERY_BATCH_SIZE = 8
_NAME = re.compile(r"[0-9a-f]{32}\.wav")
logger = logging.getLogger(__name__)


class RecoveryStopped(Exception):
    pass


class RecordingJournal:
    def __init__(self, archive):
        self.archive = archive

    @staticmethod
    def initialize(conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recording_journal (
                recording_id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
                owner_session_id TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                sample_rate INTEGER NOT NULL,
                channels INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                registered INTEGER NOT NULL DEFAULT 0,
                recovery_error TEXT,
                last_attempted_at TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recording_journal_recovery "
                     "ON recording_journal(last_attempted_at, created_at) "
                     "WHERE registered=0 OR session_id IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recording_journal_owner "
                     "ON recording_journal(session_id)")

    @staticmethod
    def _check_metadata(row) -> None:
        if (not isinstance(row["filename"], str) or not isinstance(row["sha256"], str)
                or not isinstance(row["size_bytes"], int)
                or not isinstance(row["sample_rate"], int) or not isinstance(row["channels"], int)
                or not isinstance(row["duration_seconds"], (int, float))
                or not _NAME.fullmatch(row["filename"])
                or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                or not 44 < row["size_bytes"] <= MAX_MEDIA_BYTES
                or not 8000 <= row["sample_rate"] <= 48000 or row["channels"] not in (1, 2)
                or not math.isfinite(row["duration_seconds"])
                or not 0 < row["duration_seconds"] <= MAX_DURATION_SECONDS):
            raise ValueError("invalid recording reservation metadata")

    def reserve(self, session_id: str, **metadata) -> str:
        self._check_metadata(metadata)
        recording_id = str(uuid4())
        with self.archive._connect() as conn:
            conn.execute("""
                INSERT INTO recording_journal
                (recording_id, session_id, owner_session_id, filename, size_bytes, sha256,
                 duration_seconds, sample_rate, channels, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (recording_id, session_id, session_id, metadata["filename"],
                  metadata["size_bytes"],
                  metadata["sha256"], metadata["duration_seconds"], metadata["sample_rate"],
                  metadata["channels"], datetime.now(UTC).isoformat()))
        return recording_id

    def _path(self, row, *, partial=False):
        self._check_metadata(row)
        root = self.archive.audio_root
        if root.is_symlink():
            raise ValueError("recording root must not be a symlink")
        return root / (row["filename"] + (".partial" if partial else ""))

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def _verify(self, row, path, stop: threading.Event | None):
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NONBLOCK", 0))
        entry = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode) or entry.st_size != row["size_bytes"]:
            raise ValueError("recording entry is not the expected regular file")
        fd = os.open(path, flags)
        try:
            reader = os.fdopen(fd, "rb")
        except Exception:
            os.close(fd)
            raise
        with reader:
            before = os.fstat(reader.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_size != row["size_bytes"]
                    or self._identity(before) != self._identity(entry)):
                raise ValueError("recording size or type changed")
            with wave.open(reader, "rb") as wav:
                if (wav.getcomptype() != "NONE" or wav.getsampwidth() != 2
                        or wav.getnchannels() != row["channels"]
                        or wav.getframerate() != row["sample_rate"]
                        or wav.getnframes() <= 0
                        or abs(wav.getnframes() / wav.getframerate()
                               - row["duration_seconds"]) > 1 / row["sample_rate"]):
                    raise ValueError("recording WAV metadata changed")
                wav.setpos(wav.getnframes() - 1)
                if len(wav.readframes(1)) != 2 * row["channels"]:
                    raise ValueError("recording PCM payload is truncated")
            reader.seek(0)
            digest = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                if stop is not None and stop.is_set():
                    raise RecoveryStopped
                block = reader.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("recording is truncated")
                digest.update(block)
                remaining -= len(block)
            after = os.fstat(reader.fileno())
            if (self._identity(before) != self._identity(after)
                    or digest.hexdigest() != row["sha256"]):
                raise ValueError("recording content changed")
        return self._identity(after)

    def _row(self, conn, recording_id):
        return conn.execute("SELECT * FROM recording_journal WHERE recording_id=?",
                            (recording_id,)).fetchone()

    @staticmethod
    def _register(conn, row):
        conn.execute("""
            INSERT INTO session_recordings
            (recording_id, session_id, filename, media_type, size_bytes, sha256,
             duration_seconds, sample_rate, channels, created_at)
            VALUES (?, ?, ?, 'audio/wav', ?, ?, ?, ?, ?, ?)
        """, (row["recording_id"], row["session_id"], row["filename"], row["size_bytes"],
              row["sha256"], row["duration_seconds"], row["sample_rate"], row["channels"],
              row["created_at"]))
        conn.execute("UPDATE recording_journal SET registered=1, recovery_error=NULL "
                     "WHERE recording_id=?", (row["recording_id"],))

    @staticmethod
    def _registered(conn, recording_id):
        row = conn.execute(
            "SELECT recording_id, session_id, media_type, size_bytes, sha256, "
            "duration_seconds, sample_rate, channels, created_at FROM session_recordings "
            "WHERE recording_id=?", (recording_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _finish(self, recording_id: str, *, promote=False, stop=None):
        with self.archive._connect() as conn:
            row = self._row(conn, recording_id)
            if row is None:
                return self._registered(conn, recording_id)
            if row["session_id"] is not None and row["registered"]:
                return self._registered(conn, recording_id)
        if row["session_id"] is not None and row["session_id"] != row["owner_session_id"]:
            raise ValueError("recording ownership changed")
        if row["session_id"] is None:
            return self._clean_deleted_owner(row, stop)
        path = self._path(row, partial=promote)
        rename = promote
        if not promote and not path.exists() and not path.is_symlink():
            # A process may stop after committing ownership but before rename.
            # Only this journal's fully prepared partial may be recovered.
            path = self._path(row, partial=True)
            rename = True
        identity = self._verify(row, path, stop)
        # Expensive format/hash work is outside the write transaction. The short
        # transaction serializes rename/register with FK deletion and recovery.
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._row(conn, recording_id)
            if current is None:
                return self._registered(conn, recording_id)
            if current["session_id"] is None:
                return None  # Its durable tombstone owns subsequent cleanup.
            if current["registered"]:
                return self._registered(conn, recording_id)
            if stop is not None and stop.is_set():
                raise RecoveryStopped
            if path.is_symlink() or self._identity(path.stat()) != identity:
                raise ValueError("recording entry changed after verification")
            if not promote:
                owner = conn.execute("SELECT ended_at FROM sessions WHERE session_id=?",
                                     (row["session_id"],)).fetchone()
                if owner is None or owner[0] is None:
                    raise ValueError("recording owner has not ended")
            if rename:
                final = self._path(row)
                if final.exists() or final.is_symlink():
                    raise ValueError("recording destination already exists")
                path.replace(final)
            self._register(conn, current)
            result = self._registered(conn, recording_id)
        return result

    def promote(self, recording_id: str, *, stop=None):
        return self._finish(recording_id, promote=True, stop=stop)

    def _clean_deleted_owner(self, row, stop):
        for partial in (False, True):
            path = self._path(row, partial=partial)
            if not path.exists() and not path.is_symlink():
                continue
            identity = self._verify(row, path, stop)
            with self.archive._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = self._row(conn, row["recording_id"])
                if current is None or current["session_id"] is not None:
                    return None
                if stop is not None and stop.is_set():
                    raise RecoveryStopped
                if path.is_symlink() or self._identity(path.stat()) != identity:
                    raise ValueError("recording deletion entry changed")
                path.unlink()
        with self.archive._connect() as conn:
            conn.execute("DELETE FROM recording_journal "
                         "WHERE recording_id=? AND session_id IS NULL",
                         (row["recording_id"],))
        return None

    def recover_batch(self, stop: threading.Event, *, attempted_before=None) -> int:
        cutoff = attempted_before or datetime.now(UTC).isoformat()
        with self.archive._connect() as conn:
            rows = conn.execute("""
                SELECT p.recording_id FROM recording_journal p
                LEFT JOIN sessions s ON s.session_id=p.session_id
                WHERE (p.recovery_error IS NULL OR p.recovery_error='io_deferred')
                  AND (p.registered=0 OR p.session_id IS NULL)
                  AND (p.session_id IS NULL OR s.ended_at IS NOT NULL)
                  AND p.last_attempted_at < ? AND p.created_at <= ?
                ORDER BY p.last_attempted_at, p.created_at, p.recording_id LIMIT ?
            """, (cutoff, cutoff, RECOVERY_BATCH_SIZE)).fetchall()
        processed = 0
        for row in rows:
            if stop.is_set():
                break
            try:
                self._finish(row[0], stop=stop)
            except RecoveryStopped:
                break
            except (OSError, EOFError, ValueError, sqlite3.Error, wave.Error) as exc:
                # Never destroy corrupt/unverifiable owned evidence. Retain a
                # diagnostic and leave its files for explicit operator repair.
                with self.archive._connect() as conn:
                    code = ("io_deferred" if isinstance(exc, (OSError, sqlite3.Error))
                            else "integrity_check_failed")
                    conn.execute("UPDATE recording_journal "
                                 "SET recovery_error=?,last_attempted_at=? WHERE recording_id=?",
                                 (code, datetime.now(UTC).isoformat(), row[0]))
                logger.warning("Recording recovery deferred (%s): %s", code, row[0])
            processed += 1
        return processed

