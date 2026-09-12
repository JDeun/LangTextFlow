from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _ClientFailures:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    touched_at: float = 0.0


class AudienceJoinRateLimiter:
    """Bound invalid join-code attempts without throttling authenticated audience traffic."""

    def __init__(
        self,
        *,
        max_failures: int = 8,
        window_seconds: float = 60.0,
        block_seconds: float = 300.0,
        max_clients: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1")
        if window_seconds <= 0 or block_seconds <= 0:
            raise ValueError("rate-limit durations must be positive")
        if max_clients < 1:
            raise ValueError("max_clients must be at least 1")
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self.max_clients = max_clients
        self._clock = clock
        self._clients: dict[str, _ClientFailures] = {}

    def retry_after(self, client_host: str | None) -> int | None:
        key = self._key(client_host)
        now = self._clock()
        state = self._clients.get(key)
        if state is None:
            return None
        state.touched_at = now
        self._prune_failures(state, now)
        if state.blocked_until <= now:
            state.blocked_until = 0.0
            if not state.failures:
                self._clients.pop(key, None)
            return None
        return max(1, math.ceil(state.blocked_until - now))

    def record_failure(self, client_host: str | None) -> int | None:
        key = self._key(client_host)
        now = self._clock()
        state = self._clients.setdefault(key, _ClientFailures())
        state.touched_at = now
        self._prune_failures(state, now)
        if state.blocked_until > now:
            return max(1, math.ceil(state.blocked_until - now))

        state.failures.append(now)
        if len(state.failures) >= self.max_failures:
            state.failures.clear()
            state.blocked_until = now + self.block_seconds
            retry_after = max(1, math.ceil(self.block_seconds))
        else:
            retry_after = None
        self._trim_clients()
        return retry_after

    def record_success(self, client_host: str | None) -> None:
        self._clients.pop(self._key(client_host), None)

    def tracked_clients(self) -> int:
        return len(self._clients)

    def _prune_failures(self, state: _ClientFailures, now: float) -> None:
        cutoff = now - self.window_seconds
        while state.failures and state.failures[0] <= cutoff:
            state.failures.popleft()

    def _trim_clients(self) -> None:
        overflow = len(self._clients) - self.max_clients
        if overflow <= 0:
            return
        oldest = sorted(self._clients.items(), key=lambda item: item[1].touched_at)
        for key, _ in oldest[:overflow]:
            self._clients.pop(key, None)

    @staticmethod
    def _key(client_host: str | None) -> str:
        normalized = (client_host or "unknown").strip().casefold()
        return normalized or "unknown"
