from __future__ import annotations

import pytest
from fastapi.responses import Response
from starlette.requests import Request

from langtextflow import main


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("192.168.1.12", 1234),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_audience_writes_rejected_before_downstream(method: str) -> None:
    called = False

    async def downstream(request: Request) -> Response:
        nonlocal called
        called = True
        return Response(status_code=204)

    result = await main.reject_remote_operator_api_before_body_parse(
        _request(method, "/api/v1/audience/ABC123/captions"), downstream
    )
    assert result.status_code == 405
    assert result.headers["allow"] == "GET, HEAD, OPTIONS"
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_audience_reads_reach_downstream(method: str) -> None:
    called = False

    async def downstream(request: Request) -> Response:
        nonlocal called
        called = True
        return Response(status_code=204)

    result = await main.reject_remote_operator_api_before_body_parse(
        _request(method, "/api/v1/audience/ABC123"), downstream
    )
    assert result.status_code == 204
    assert called
