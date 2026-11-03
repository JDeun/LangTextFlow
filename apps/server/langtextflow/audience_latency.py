"""Bounded, session-local aggregation of privacy-minimal audience render estimates.

The API enforces per-session operator opt-in, audience consent and authorization.
Only bounded server-monotonic sample ages are kept alongside numeric estimates.
"""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

Stage = Literal["partial", "stable", "translated", "committed"]
ALLOWED_STAGES = frozenset(("partial", "stable", "translated", "committed"))
MAX_LATENCY_MS = 60_000.0


def _quantile(sorted_values: list[float], p: float) -> float:
    index = (len(sorted_values) - 1) * p
    low = int(index)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (index - low)


@dataclass
class AudienceLatencyAggregate:
    """Recent samples only; never stores transcript or device identifiers."""

    capacity_per_stage: int = 512
    window_seconds: float = 60
    clock: Callable[[], float] = time.monotonic
    _samples: dict[str, deque[tuple[float, float]]] = field(default_factory=dict, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.capacity_per_stage, bool)
            or not isinstance(self.capacity_per_stage, int)
            or self.capacity_per_stage < 1
        ):
            raise ValueError("capacity_per_stage must be a positive integer")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("window_seconds must be finite and positive")

    def add(self, stage: str, receive_to_display_ms: float) -> bool:
        if stage not in ALLOWED_STAGES:
            return False
        if (
            isinstance(receive_to_display_ms, bool)
            or not isinstance(receive_to_display_ms, (int, float))
        ):
            return False
        if (
            not math.isfinite(receive_to_display_ms)
            or not 0 <= receive_to_display_ms <= MAX_LATENCY_MS
        ):
            return False
        with self._lock:
            samples = self._samples.setdefault(stage, deque(maxlen=self.capacity_per_stage))
            samples.append((self.clock(), float(receive_to_display_ms)))
        return True

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        with self._lock:
            cutoff = self.clock() - self.window_seconds
            copied = {}
            for stage, samples in self._samples.items():
                while samples and samples[0][0] <= cutoff:
                    samples.popleft()
                copied[stage] = sorted(value for _, value in samples)
        for stage, sorted_values in copied.items():
            if not sorted_values:
                continue
            result[stage] = {
                "count": len(sorted_values),
                "p50_ms": _quantile(sorted_values, 0.5),
                "p95_ms": _quantile(sorted_values, 0.95),
                "p99_ms": _quantile(sorted_values, 0.99),
            }
        return result

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
