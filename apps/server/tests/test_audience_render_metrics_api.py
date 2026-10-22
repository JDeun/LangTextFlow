from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from langtextflow import main


def _request(host: str = "192.168.1.9") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/metrics/audience-render",
        "headers": [],
        "client": (host, 12345),
        "scheme": "http",
        "server": ("127.0.0.1", 8000),
    })


@pytest.mark.asyncio
async def test_audience_render_metrics_rejects_non_operator() -> None:
    with pytest.raises(HTTPException) as exc:
        await main.audience_render_metrics(_request())
    assert exc.value.status_code in (401, 403)


@pytest.mark.asyncio
async def test_audience_render_metrics_empty_snapshot_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_require_operator", lambda request: None)
    result = await main.audience_render_metrics(_request())
    assert result["scope"] == "audience"
    assert result["reporting_enabled"] is False
    assert result["stages"] == {}
