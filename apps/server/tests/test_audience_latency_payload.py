from __future__ import annotations

import pytest
from pydantic import ValidationError

from langtextflow.audience_latency_payload import AudienceLatencyPayload


def _payload() -> dict[str, object]:
    return {
        "stage": "translated",
        "receive_to_display_ms": 12.5,
        "timing_method": "double_raf_estimate",
    }


def test_accepts_minimal_payload() -> None:
    value = AudienceLatencyPayload.model_validate(_payload())
    assert value.stage == "translated"
    assert value.receive_to_display_ms == 12.5


@pytest.mark.parametrize("extra", ["transcript", "device_id", "timestamp", "ip"])
def test_rejects_extra_personal_or_content_fields(extra: str) -> None:
    payload = _payload()
    payload[extra] = "unexpected"
    with pytest.raises(ValidationError):
        AudienceLatencyPayload.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 60001.0, "5", True])
def test_rejects_invalid_latency(value: object) -> None:
    payload = _payload()
    payload["receive_to_display_ms"] = value
    with pytest.raises(ValidationError):
        AudienceLatencyPayload.model_validate(payload)


def test_rejects_unknown_stage_or_timing_method() -> None:
    for key, value in (("stage", "unknown"), ("timing_method", "wall_clock")):
        payload = _payload()
        payload[key] = value
        with pytest.raises(ValidationError):
            AudienceLatencyPayload.model_validate(payload)
