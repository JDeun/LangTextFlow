"""Read a sole-reader parent pipe without blocking the Windows CRT during native imports."""
from __future__ import annotations

import os
import time

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _peek = _kernel.PeekNamedPipe
    _peek.argtypes = (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                      ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                      ctypes.POINTER(wintypes.DWORD))
    _peek.restype = wintypes.BOOL


def read_parent_pipe(size: int, *, descriptor: int = 0) -> bytes:
    """Return data or terminal EOF/error; exactly one reader must own the pipe."""
    if size <= 0:
        raise ValueError("positive pipe read size required")
    if os.name != "nt":
        return os.read(descriptor, size)
    try:
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        available = wintypes.DWORD()
        while True:
            if not _peek(handle, None, 0, None, ctypes.byref(available), None):
                return b""  # Broken/invalid parent handles must also stop native work.
            if available.value:
                # A blocking CRT read can deadlock native imports that inspect/duplicate fd0.
                return os.read(descriptor, min(size, available.value))
            time.sleep(.01)
    except OSError:
        return b""
