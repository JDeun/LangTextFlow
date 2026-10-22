"""In-memory, bounded rate policy for future opt-in audience telemetry ingestion.

Network identity is only used as an ephemeral rate-limit key and never
included in aggregate samples or serialized responses.
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class AudienceMetricRateLimiter:
    max_events: int = 12
    window_seconds: float = 60.0
    max_clients: int = 1024
    clock: Callable[[], float] = time.monotonic
    _events: OrderedDict[str, tuple[float, int]] = field(
        default_factory=OrderedDict, init=False
    )
    _lock: Lock = field(default_factory=Lock, init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_events, bool)
            or not isinstance(self.max_events, int)
            or self.max_events < 1
            or isinstance(self.max_clients, bool)
            or not isinstance(self.max_clients, int)
            or self.max_clients < 1
            or not math.isfinite(self.window_seconds)
            or self.window_seconds <= 0
        ):
            raise ValueError("invalid audience metric rate-limit configuration")

    def allow(self, client_key: str | None) -> bool:
        if not client_key:
            return False
        now = self.clock()
        with self._lock:
            previous = self._events.pop(client_key, None)
            if previous is None or now - previous[0] >= self.window_seconds:
                start, count = now, 0
            else:
                start, count = previous
            allowed = count < self.max_events
            self._events[client_key] = (start, count + 1 if allowed else count)
            while len(self._events) > self.max_clients:
                self._events.popitem(last=False)
            return allowed

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
