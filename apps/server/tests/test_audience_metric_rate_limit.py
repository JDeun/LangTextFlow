from __future__ import annotations

from langtextflow.audience_metric_rate_limit import AudienceMetricRateLimiter


def test_limiter_rejects_missing_identity() -> None:
    limiter = AudienceMetricRateLimiter()
    assert not limiter.allow(None)
    assert not limiter.allow("")


def test_limiter_enforces_window_and_reset() -> None:
    now = [0.0]
    limiter = AudienceMetricRateLimiter(
        max_events=2, window_seconds=10, clock=lambda: now[0]
    )
    assert limiter.allow("client")
    assert limiter.allow("client")
    assert not limiter.allow("client")
    now[0] = 10.0
    assert limiter.allow("client")
    limiter.reset()
    assert limiter.allow("client")


def test_limiter_bounds_memory() -> None:
    limiter = AudienceMetricRateLimiter(max_clients=2, max_events=1)
    assert limiter.allow("a")
    assert limiter.allow("b")
    assert limiter.allow("c")
    assert len(limiter._events) == 2
    assert limiter.allow("a")


def test_limiter_rejects_invalid_configuration() -> None:
    for config in ({"max_events": 0}, {"max_clients": 0}, {"window_seconds": 0}):
        try:
            AudienceMetricRateLimiter(**config)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid config rejection: {config}")
