"""Bound provider JSON before parsing, including metadata and ignored envelope fields."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from .prompt_safety import MAX_CORRECTION_RESPONSE_CHARS

# The largest allowed model text can escape each Unicode scalar as two JSON
# surrogate escapes (12 bytes). Leave bounded room for the provider envelope.
MAX_PROVIDER_RESPONSE_BYTES = MAX_CORRECTION_RESPONSE_CHARS * 12 + 64 * 1024
READ_CHUNK_BYTES = 16 * 1024


@asynccontextmanager
async def provider_stream(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any,
) -> AsyncIterator[httpx.Response]:
    """Shared status, encoding and error policy for bounded provider readers."""
    headers = httpx.Headers(kwargs.pop("headers", None))
    headers["Accept-Encoding"] = "identity"
    try:
        async with client.stream(
            method, url, headers=headers, follow_redirects=False, **kwargs,
        ) as response:
            if not 200 <= response.status_code < 300:
                # HTTPStatusError includes credentials/query strings in its URL.
                raise ValueError(f"provider returned HTTP {response.status_code}")
            # Never inflate a compressed chunk ahead of the reader's byte check.
            encoding = response.headers.get("content-encoding", "identity").strip().casefold()
            if encoding != "identity":
                raise ValueError("provider response must use identity content encoding")
            yield response
    except httpx.TimeoutException as exc:
        raise httpx.TimeoutException("provider HTTP request timed out") from exc
    except (httpx.RequestError, httpx.InvalidURL) as exc:
        raise ValueError("provider HTTP request failed") from exc


async def bounded_json_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any,
) -> Any:
    async with provider_stream(client, method, url, **kwargs) as response:
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


async def bounded_response_lines(response: httpx.Response) -> AsyncIterator[str]:
    """Bound each UTF-8 progress line, without bounding a download's total stream."""
    line = bytearray()
    # Keep small progress messages immediate: a fixed aiter_bytes chunk_size
    # buffers until that size or EOF. Split each bounded transport read instead.
    async for received in response.aiter_bytes():
        for offset in range(0, len(received), READ_CHUNK_BYTES):
            parts = received[offset:offset + READ_CHUNK_BYTES].split(b"\n")
            for index, part in enumerate(parts):
                if len(line) + len(part) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise ValueError("provider progress line exceeds the safety byte limit")
                line.extend(part)
                if index < len(parts) - 1:
                    yield _decode_progress_line(line)
                    line.clear()
    if line:
        yield _decode_progress_line(line)


def _decode_progress_line(line: bytearray) -> str:
    try:
        return line.decode("utf-8").removesuffix("\r")
    except UnicodeError as exc:
        raise ValueError("provider progress line is not valid UTF-8") from exc
