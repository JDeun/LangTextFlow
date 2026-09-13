from __future__ import annotations

import httpx
import pytest

from langtextflow.http_safety import iter_bounded_lines, read_bounded_json


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_bounded_json_accepts_small_streamed_payload() -> None:
    response = httpx.Response(
        200,
        stream=ChunkedStream([b'{"sample_', b'rate":16000}']),
    )
    payload = await read_bounded_json(response, max_bytes=64, label="config")
    assert payload == {"sample_rate": 16000}


@pytest.mark.asyncio
async def test_bounded_json_rejects_declared_or_streamed_oversize() -> None:
    declared = httpx.Response(
        200,
        headers={"content-length": "1000"},
        stream=ChunkedStream([b"{}"]),
    )
    with pytest.raises(ValueError, match="safety limit"):
        await read_bounded_json(declared, max_bytes=64, label="config")

    streamed = httpx.Response(
        200,
        stream=ChunkedStream([b"{" + b"x" * 80 + b"}"]),
    )
    with pytest.raises(ValueError, match="safety limit"):
        await read_bounded_json(streamed, max_bytes=64, label="config")


@pytest.mark.asyncio
async def test_bounded_json_rejects_invalid_utf8_json() -> None:
    response = httpx.Response(200, stream=ChunkedStream([b"\xff\xfe"]))
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        await read_bounded_json(response, max_bytes=64, label="config")


@pytest.mark.asyncio
async def test_bounded_lines_handles_split_records_and_rejects_long_record() -> None:
    response = httpx.Response(
        200,
        stream=ChunkedStream([b'{"a":1}\n{"b"', b':2}\n']),
    )
    lines = [line async for line in iter_bounded_lines(response, max_line_bytes=32)]
    assert lines == ['{"a":1}', '{"b":2}']

    oversized = httpx.Response(
        200,
        stream=ChunkedStream([b"x" * 33]),
    )
    with pytest.raises(ValueError, match="safety limit"):
        _ = [line async for line in iter_bounded_lines(oversized, max_line_bytes=32)]
