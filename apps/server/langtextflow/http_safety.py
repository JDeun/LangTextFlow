from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

DEFAULT_JSON_RESPONSE_BYTES = 1024 * 1024
DEFAULT_STREAM_LINE_BYTES = 64 * 1024
_STREAM_CHUNK_BYTES = 16 * 1024


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


async def read_bounded_bytes(
    response: httpx.Response,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Read a streamed HTTP response while enforcing a decoded-byte ceiling."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    declared = _content_length(response)
    if declared is not None and declared > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte safety limit")

    data = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=_STREAM_CHUNK_BYTES):
        if len(data) + len(chunk) > max_bytes:
            raise ValueError(f"{label} exceeds the {max_bytes}-byte safety limit")
        data.extend(chunk)
    return bytes(data)


async def read_bounded_json(
    response: httpx.Response,
    *,
    max_bytes: int = DEFAULT_JSON_RESPONSE_BYTES,
    label: str = "provider JSON response",
) -> Any:
    raw = await read_bounded_bytes(response, max_bytes=max_bytes, label=label)
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc


async def iter_bounded_lines(
    response: httpx.Response,
    *,
    max_line_bytes: int = DEFAULT_STREAM_LINE_BYTES,
    label: str = "provider stream line",
) -> AsyncIterator[str]:
    """Yield UTF-8 lines without allowing one stream record to grow unbounded."""

    if max_line_bytes < 1:
        raise ValueError("max_line_bytes must be positive")

    buffer = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=_STREAM_CHUNK_BYTES):
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            if newline > max_line_bytes:
                raise ValueError(f"{label} exceeds the {max_line_bytes}-byte safety limit")
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            try:
                yield line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{label} is not valid UTF-8") from exc
        if len(buffer) > max_line_bytes:
            raise ValueError(f"{label} exceeds the {max_line_bytes}-byte safety limit")

    if buffer:
        if len(buffer) > max_line_bytes:
            raise ValueError(f"{label} exceeds the {max_line_bytes}-byte safety limit")
        try:
            yield bytes(buffer).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{label} is not valid UTF-8") from exc
