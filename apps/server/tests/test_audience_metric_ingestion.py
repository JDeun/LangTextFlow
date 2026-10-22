from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from langtextflow import main
from langtextflow.audience_latency_payload import AudienceLatencyPayload


def _request(
    method: str = "POST", content_length: str | None = "120"
) -> Request:
    headers = [(b"x-audience-metrics-consent", b"true")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1/audience/ABC123/metrics/render",
            "raw_path": b"/api/v1/audience/ABC123/metrics/render",
            "query_string": b"",
            "headers": headers,
            "client": ("192.168.1.10", 1200),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
async def test_metric_route_disabled_by_default() -> None:
    payload = AudienceLatencyPayload(
        stage="stable",
        receive_to_display_ms=20.0,
        timing_method="double_raf_estimate",
    )
    with pytest.raises(HTTPException) as error:
        await main.submit_audience_render_metric(_request(), "ABC123", payload)
    assert error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [None, "513", "abc", "-1"])
async def test_metric_request_length_gate(
    monkeypatch: pytest.MonkeyPatch, length: str | None
) -> None:
    monkeypatch.setattr(main, "_audience_metrics_collection_enabled", True)
    called = False

    async def downstream(request: Request) -> Response:
        nonlocal called
        called = True
        return Response(status_code=204)

    result = await main.reject_remote_operator_api_before_body_parse(
        _request(content_length=length), downstream
    )
    assert result.status_code == 413
    assert not called


@pytest.mark.asyncio
async def test_disabled_metric_request_does_not_reach_downstream() -> None:
    called = False

    async def downstream(request: Request) -> Response:
        nonlocal called
        called = True
        return Response(status_code=204)

    result = await main.reject_remote_operator_api_before_body_parse(
        _request(), downstream
    )
    assert result.status_code == 403
    assert not called


@pytest.mark.asyncio
async def test_metric_body_replayed_after_bounded_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_audience_metrics_collection_enabled", True)
    body = b'{"stage":"stable"}'
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    request = _request(content_length=str(len(body)))
    request._receive = receive

    async def downstream(request: Request) -> Response:
        assert await request.body() == body
        return Response(status_code=204)

    result = await main.reject_remote_operator_api_before_body_parse(
        request, downstream
    )
    assert result.status_code == 204


@pytest.mark.asyncio
async def test_metric_actual_body_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_audience_metrics_collection_enabled", True)
    body = b"x" * 513

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    request = _request(content_length="10")
    request._receive = receive

    async def downstream(request: Request) -> Response:
        pytest.fail("oversized body reached downstream")

    result = await main.reject_remote_operator_api_before_body_parse(
        request, downstream
    )
    assert result.status_code == 413
