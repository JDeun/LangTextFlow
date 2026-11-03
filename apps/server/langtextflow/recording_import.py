"""Own one authorized WAV import across thread cancellation and archive promotion."""
from __future__ import annotations

import hashlib
import os
import stat
import threading
from contextlib import suppress
from uuid import uuid4

from .recording_journal import MAX_MEDIA_BYTES, RecoveryStopped


class RecordingImport:
    def __init__(self, archive, session_id, validate):
        self.archive, self.session_id, self.validate = archive, session_id, validate
        self.filename = f"{uuid4().hex}.wav"
        self.partial = archive.audio_root / (self.filename + ".partial")
        self.final = archive.audio_root / self.filename
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._stream = None
        self._identity = None
        self._reservation = None
        self._size = 0
        self._digest = hashlib.sha256()

    def open(self):
        with self._lock:
            if self.cancelled.is_set():
                raise RecoveryStopped
            if self.archive.audio_root.is_symlink():
                raise ValueError("recording root must not be a symlink")
            fd = os.open(self.partial, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            info = os.fstat(fd)
            self._identity = info.st_dev, info.st_ino
            try:
                self._stream = os.fdopen(fd, "w+b")
            except Exception:
                os.close(fd)
                self._abort_locked()
                raise
            self._check_cancelled()

    def write(self, data):
        with self._lock:
            self._check_cancelled()
            if self._size + len(data) > MAX_MEDIA_BYTES:
                raise ValueError("recording exceeds upload limit")
            self._stream.write(data)
            self._size += len(data)
            self._digest.update(data)
            self._check_cancelled()

    def finish(self):
        with self._lock:
            self._check_cancelled()
            self._stream.flush()
            os.fsync(self._stream.fileno())
            if os.fstat(self._stream.fileno()).st_size != self._size:
                raise ValueError("recording import size changed")
            self._stream.seek(0)
            channels, sample_rate, duration = self.validate(self._stream, stop=self.cancelled)
            self._stream.close()
            self._stream = None
            self._check_cancelled()
            self._reservation = self.archive.recording_journal.reserve(
                self.session_id, filename=self.filename, size_bytes=self._size,
                sha256=self._digest.hexdigest(), duration_seconds=duration,
                sample_rate=sample_rate, channels=channels,
            )
            result = self.archive.recording_journal.promote(
                self._reservation, stop=self.cancelled,
            )
            self._check_cancelled()
            if result is None:
                raise ValueError("recording owner was removed")
            return result

    def _owned(self, path):
        with suppress(OSError):
            info = path.stat(follow_symlinks=False)
            return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == self._identity
        return False

    def _check_cancelled(self):
        if self.cancelled.is_set():
            self._abort_locked()
            raise RecoveryStopped

    def _abort_locked(self):
        if self._stream is not None:
            with suppress(OSError):
                self._stream.close()
            self._stream = None
        if self._owned(self.partial):
            self.partial.unlink(missing_ok=True)
        # A renamed/registered asset has durable ownership and must survive a
        # lost HTTP acknowledgement. Incomplete private imports are discarded.
        if (self._reservation and not self._owned(self.final)
                and not self.partial.exists() and not self.partial.is_symlink()
                and not self.final.exists() and not self.final.is_symlink()):
            with self.archive._connect() as conn:
                conn.execute("DELETE FROM recording_journal WHERE recording_id=? AND registered=0",
                             (self._reservation,))

    def abort(self):
        self.cancelled.set()  # Native open/write/finish also observe this fence.
        with self._lock:
            self._abort_locked()
