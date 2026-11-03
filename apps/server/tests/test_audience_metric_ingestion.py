"""Opt-in LAN telemetry validates consent, bodies, rate bounds and session transitions."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from langtextflow import main
from langtextflow.audience_latency import AudienceLatencyAggregate
from langtextflow.audience_metric_rate_limit import AudienceMetricRateLimiter
from langtextflow.audience_security import AudienceJoinRateLimiter
from langtextflow.config import Settings
from langtextflow.models import SessionContext, SessionState
from langtextflow.runtime import CaptionRuntime

PATH = "/api/v1/audience/ABC123/metrics/render"
HEADERS = {"X-Audience-Metrics-Consent": "true", "Content-Type": "application/json"}
PAYLOAD = {"stage": "stable", "receive_to_display_ms": 24.0,
           "timing_method": "double_raf_estimate"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "metrics.db")))
    runtime.state = SessionState(
        session_id="active", join_code="ABC123", running=True,
        collect_audience_metrics=True, context=SessionContext(),
    )
    monkeypatch.setattr(main, "runtime", runtime)
    monkeypatch.setattr(main, "audience_latency_aggregate", AudienceLatencyAggregate())
    monkeypatch.setattr(main, "audience_metric_limiter", AudienceMetricRateLimiter())
    monkeypatch.setattr(main, "audience_join_limiter", AudienceJoinRateLimiter())
    test_client = TestClient(main.app, client=("192.168.1.12", 4000))
    yield test_client
    test_client.close()


def test_numeric_only_reports_are_authorized_and_operator_visible(client):
    assert client.post(PATH, headers=HEADERS, json=PAYLOAD).status_code == 204
    assert main.audience_latency_aggregate.snapshot()["stable"]["p95_ms"] == 24
    # Audience cannot inspect aggregate data or any operator controls.
    assert client.get("/api/v1/metrics/audience-render").status_code == 403
    local = TestClient(main.app, client=("127.0.0.1", 4000))
    report = local.get("/api/v1/metrics/audience-render").json()
    assert report["reporting_enabled"] is True
    assert report["window_seconds"] == 60
    assert report["stages"]["stable"]["count"] == 1
    assert "192.168" not in json.dumps(report)


def test_disabled_or_missing_consent_rejects_before_json_parsing(client):
    assert client.post(PATH, json=PAYLOAD).status_code == 403
    main.runtime.state.collect_audience_metrics = False
    assert client.post(PATH, headers=HEADERS, content=b"not-json").status_code == 403
    assert main.audience_latency_aggregate.snapshot() == {}


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"},
])
def test_cross_site_reports_are_rejected(client, headers):
    assert client.post(PATH, headers=HEADERS | headers, json=PAYLOAD).status_code == 403


@pytest.mark.parametrize("extra", [
    {"text": "private caption"}, {"device_id": "private"}, {"timestamp": 123},
    {"stage": "unknown"}, {"receive_to_display_ms": -1},
    {"receive_to_display_ms": 60001}, {"receive_to_display_ms": True},
])
def test_unknown_or_invalid_fields_never_enter_aggregates(client, extra):
    assert client.post(PATH, headers=HEADERS, json=PAYLOAD | extra).status_code == 422
    assert main.audience_latency_aggregate.snapshot() == {}


def test_stream_and_declared_lengths_are_bounded(client):
    assert client.post(PATH, headers=HEADERS, content=b"x" * 513).status_code == 413
    assert client.post(
        PATH, headers=HEADERS, content=iter([b"x" * 300, b"y" * 300]),
    ).status_code == 413
    assert client.post(
        PATH, headers=HEADERS | {"Content-Length": "bad"}, content=b"{}",
    ).status_code == 413
    assert client.post(
        PATH, headers=HEADERS | {"Content-Length": "1"}, json=PAYLOAD,
    ).status_code == 400


def test_invalid_join_and_rate_limit_fail_closed(client):
    assert client.post(
        PATH.replace("ABC123", "WRONG1"), headers=HEADERS, json=PAYLOAD,
    ).status_code == 404
    for _ in range(12):
        assert client.post(PATH, headers=HEADERS, json=PAYLOAD).status_code == 204
    assert client.post(PATH, headers=HEADERS, json=PAYLOAD).status_code == 429
    assert main.audience_latency_aggregate.snapshot()["stable"]["count"] == 12


@pytest.mark.asyncio
async def test_restart_while_body_arrives_does_not_pollute_new_session(client):
    async def receive():
        main.runtime.state = main.runtime.state.model_copy(update={
            "session_id": "new-session", "join_code": "NEW123",
        })
        return {"type": "http.request", "body": json.dumps(PAYLOAD).encode(), "more_body": False}

    request = Request({
        "type": "http", "method": "POST", "path": PATH,
        "headers": [(key.lower().encode(), value.encode()) for key, value in HEADERS.items()],
        "client": ("192.168.1.12", 4000),
    }, receive=receive)
    with pytest.raises(HTTPException) as error:
        await main.submit_audience_render_metric(request, "ABC123")
    assert error.value.status_code == 409
    assert main.audience_latency_aggregate.snapshot() == {}


@pytest.mark.asyncio
async def test_slow_body_expires_without_blocking_caption_lifecycle(client):
    async def receive():
        await asyncio.sleep(3)
        pytest.fail("body timeout did not interrupt the slow reader")

    request = Request({
        "type": "http", "method": "POST", "path": PATH,
        "headers": [(key.lower().encode(), value.encode()) for key, value in HEADERS.items()],
        "client": ("192.168.1.12", 4000),
    }, receive=receive)
    with pytest.raises(HTTPException) as error:
        await main.submit_audience_render_metric(request, "ABC123")
    assert error.value.status_code == 408
    assert main.runtime.state.running
    assert not main._audio_transition_lock.locked()
    assert main.audience_latency_aggregate.snapshot() == {}
