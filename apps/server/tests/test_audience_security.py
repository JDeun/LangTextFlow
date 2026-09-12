from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import HTTPException

from langtextflow import main
from langtextflow.audience_security import AudienceJoinRateLimiter


@dataclass
class FakeClock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_invalid_attempts_trigger_temporary_block() -> None:
    clock = FakeClock()
    limiter = AudienceJoinRateLimiter(
        max_failures=3,
        window_seconds=60,
        block_seconds=120,
        clock=clock,
    )

    assert limiter.record_failure("192.168.1.8") is None
    assert limiter.record_failure("192.168.1.8") is None
    assert limiter.record_failure("192.168.1.8") == 120
    assert limiter.retry_after("192.168.1.8") == 120

    clock.advance(119.2)
    assert limiter.retry_after("192.168.1.8") == 1
    clock.advance(0.8)
    assert limiter.retry_after("192.168.1.8") is None


def test_failure_window_expires_old_attempts() -> None:
    clock = FakeClock()
    limiter = AudienceJoinRateLimiter(
        max_failures=3,
        window_seconds=10,
        block_seconds=60,
        clock=clock,
    )

    assert limiter.record_failure("client") is None
    assert limiter.record_failure("client") is None
    clock.advance(10.1)
    assert limiter.record_failure("client") is None
    assert limiter.retry_after("client") is None


def test_success_clears_previous_failures() -> None:
    clock = FakeClock()
    limiter = AudienceJoinRateLimiter(
        max_failures=2,
        window_seconds=60,
        block_seconds=60,
        clock=clock,
    )

    assert limiter.record_failure("client") is None
    limiter.record_success("client")
    assert limiter.tracked_clients() == 0
    assert limiter.record_failure("client") is None


def test_limiter_caps_tracked_client_memory() -> None:
    clock = FakeClock()
    limiter = AudienceJoinRateLimiter(
        max_failures=10,
        window_seconds=60,
        block_seconds=60,
        max_clients=2,
        clock=clock,
    )

    limiter.record_failure("a")
    clock.advance(1)
    limiter.record_failure("b")
    clock.advance(1)
    limiter.record_failure("c")

    assert limiter.tracked_clients() == 2
    assert limiter.retry_after("a") is None


def test_main_authorizer_returns_429_on_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    limiter = AudienceJoinRateLimiter(
        max_failures=2,
        window_seconds=60,
        block_seconds=90,
        clock=clock,
    )

    def invalid_view(join_code: str):
        del join_code
        raise KeyError("invalid")

    monkeypatch.setattr(main, "audience_join_limiter", limiter)
    monkeypatch.setattr(main.runtime, "audience_view", invalid_view)

    with pytest.raises(HTTPException) as first:
        main._authorize_audience("10.0.0.7", "BAD001")
    assert first.value.status_code == 404

    with pytest.raises(HTTPException) as second:
        main._authorize_audience("10.0.0.7", "BAD002")
    assert second.value.status_code == 429
    assert second.value.headers == {"Retry-After": "90"}

    with pytest.raises(HTTPException) as blocked:
        main._authorize_audience("10.0.0.7", "BAD003")
    assert blocked.value.status_code == 429


def test_main_authorizer_valid_code_clears_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    limiter = AudienceJoinRateLimiter(
        max_failures=3,
        window_seconds=60,
        block_seconds=90,
        clock=clock,
    )
    valid = object()

    def view(join_code: str):
        if join_code == "RIGHT1":
            return valid
        raise KeyError("invalid")

    monkeypatch.setattr(main, "audience_join_limiter", limiter)
    monkeypatch.setattr(main.runtime, "audience_view", view)

    with pytest.raises(HTTPException):
        main._authorize_audience("100.64.1.2", "WRONG1")
    assert limiter.tracked_clients() == 1

    assert main._authorize_audience("100.64.1.2", "RIGHT1") is valid
    assert limiter.tracked_clients() == 0
