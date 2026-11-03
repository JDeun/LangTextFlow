"""Bound provider JSON before parsing, including metadata and ignored envelope fields."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .prompt_safety import MAX_CORRECTION_RESPONSE_CHARS

# The largest allowed model text can escape each Unicode scalar as two JSON
# surrogate escapes (12 bytes). Leave bounded room for the provider envelope.
MAX_PROVIDER_RESPONSE_BYTES = MAX_CORRECTION_RESPONSE_CHARS * 12 + 64 * 1024
READ_CHUNK_BYTES = 16 * 1024


async def bounded_json_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any,
) -> Any:
    headers = httpx.Headers(kwargs.pop("headers", None))
    headers["Accept-Encoding"] = "identity"
    try:
        return await _read_json(client, method, url, headers=headers, **kwargs)
    except httpx.TimeoutException as exc:
        raise httpx.TimeoutException("provider HTTP request timed out") from exc
    except (httpx.RequestError, httpx.InvalidURL) as exc:
        raise ValueError("provider HTTP request failed") from exc


async def _read_json(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Any:
    async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
        if not 200 <= response.status_code < 300:
            # HTTPStatusError includes the request URL, possibly credentials or
            # query secrets. Provider status/error surfaces need only the code.
            raise ValueError(f"provider returned HTTP {response.status_code}")
        # A decoder can allocate an unbounded expanded body before yielding a
        # chunk. Require the requested identity encoding instead of inflating it.
        if response.headers.get("content-encoding", "identity").strip().casefold() != "identity":
            raise ValueError("provider response must use identity content encoding")
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError as exc:
                raise ValueError("provider response has invalid content length") from exc
            if length < 0:
                raise ValueError("provider response has invalid content length")
            if length > MAX_PROVIDER_RESPONSE_BYTES:
                raise ValueError("provider response exceeds the safety byte limit")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=READ_CHUNK_BYTES):
            if len(body) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
                raise ValueError("provider response exceeds the safety byte limit")
            body.extend(chunk)
        try:
            return json.loads(body)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("provider response is not valid JSON") from exc
