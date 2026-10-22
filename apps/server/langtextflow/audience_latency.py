"""Bounded, session-local aggregation of privacy-minimal audience render estimates.

This module has no network endpoint: ingestion requires explicit authorization
and consent policy at the API boundary before deployment.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
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
    _samples: dict[str, deque[float]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if isinstance(self.capacity_per_stage, bool) or not isinstance(self.capacity_per_stage, int) or self.capacity_per_stage < 1:
            raise ValueError("capacity_per_stage must be a positive integer")

    def add(self, stage: str, receive_to_display_ms: float) -> bool:
        if stage not in ALLOWED_STAGES:
            return False
        if isinstance(receive_to_display_ms, bool) or not isinstance(receive_to_display_ms, (int, float)):
            return False
        if not math.isfinite(receive_to_display_ms) or not 0 <= receive_to_display_ms <= MAX_LATENCY_MS:
            return False
        samples = self._samples.setdefault(stage, deque(maxlen=self.capacity_per_stage))
        samples.append(float(receive_to_display_ms))
        return True

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        for stage, samples in self._samples.items():
            if not samples:
                continue
            sorted_values = sorted(samples)
            result[stage] = {
                "count": len(sorted_values),
                "p50_ms": _quantile(sorted_values, 0.5),
                "p95_ms": _quantile(sorted_values, 0.95),
                "p99_ms": _quantile(sorted_values, 0.99),
            }
        return result

    def reset(self) -> None:
        self._samples.clear()
