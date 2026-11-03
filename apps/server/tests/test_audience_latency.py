import pytest

from langtextflow.audience_latency import AudienceLatencyAggregate


def test_bounded_per_stage_quantiles() -> None:
    aggregate = AudienceLatencyAggregate(capacity_per_stage=3)
    for value in (1, 2, 3, 4):
        assert aggregate.add("translated", value)
    assert aggregate.snapshot()["translated"] == {
        "count": 3,
        "p50_ms": 3.0,
        "p95_ms": pytest.approx(3.9),
        "p99_ms": pytest.approx(3.98),
    }
    assert aggregate.add("stable", 10)
    assert aggregate.snapshot()["stable"]["count"] == 1
    aggregate.reset()
    assert aggregate.snapshot() == {}


@pytest.mark.parametrize("stage,value", [
    ("invalid", 1), ("translated", -1), ("translated", 60_001),
    ("translated", float("nan")), ("translated", float("inf")),
    ("translated", True), ("translated", "10"),
])
def test_invalid_measurements_are_not_recorded(stage: str, value: object) -> None:
    aggregate = AudienceLatencyAggregate()
    assert not aggregate.add(stage, value)  # type: ignore[arg-type]
    assert aggregate.snapshot() == {}


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_invalid_capacity_rejected(capacity: object) -> None:
    with pytest.raises(ValueError):
        AudienceLatencyAggregate(capacity_per_stage=capacity)  # type: ignore[arg-type]


def test_samples_expire_without_retaining_client_timestamps():
    now = [100.0]
    aggregate = AudienceLatencyAggregate(clock=lambda: now[0], window_seconds=60)
    aggregate.add("stable", 10)
    now[0] += 59
    aggregate.add("stable", 20)
    now[0] += 1
    assert aggregate.snapshot()["stable"]["count"] == 1
    now[0] += 60
    assert aggregate.snapshot() == {}


@pytest.mark.parametrize("window", [0, -1, float("nan"), float("inf")])
def test_window_is_bounded(window):
    with pytest.raises(ValueError):
        AudienceLatencyAggregate(window_seconds=window)
