from __future__ import annotations

from threading import Event

_shutdown_requested = Event()


def request_desktop_shutdown() -> None:
    _shutdown_requested.set()


def desktop_shutdown_requested() -> bool:
    return _shutdown_requested.is_set()


def reset_desktop_shutdown() -> None:
    _shutdown_requested.clear()
