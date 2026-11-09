"""Private VibeVoice transport leaf: requested immutable batches, never caption authority."""
from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import stat
import sys
import threading
from collections import deque
from contextlib import suppress

import httpx
import websockets

from .asr.vibevoice import vibevoice_ws_url
from .parent_pipe import read_parent_pipe
from .provider_http import bounded_json_request
from .worker_ipc import IDENTITY_KEYS, MAX_FRAME_BYTES, validate_inference_request

STREAM_PROTOCOL_VERSION = 1
MAX_PCM_BYTES = 1024 * 1024
MAX_BATCH_BYTES = 64 * 1024  # Leaves room for JSON-in-JSON escaping and identity.
MAX_BATCH_MESSAGES = 128
MAX_SAMPLE = (2**53) // 1000


class NoRedirectConnect(websockets.connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


def validate(request: object) -> dict:
    validate_inference_request(request, "asr")
    value = json.loads(request["payload"])
    required = {"stream_protocol_version", "candidate_epoch", "mode"}
    if (not isinstance(value, dict) or not required <= set(value)
            or type(value["stream_protocol_version"]) is not int
            or value["stream_protocol_version"] != STREAM_PROTOCOL_VERSION
            or type(value["candidate_epoch"]) is not int
            or not 1 <= value["candidate_epoch"] <= 2**53 - 1):
        raise ValueError("invalid_stream_request")
    mode = value["mode"]
    extra = ({"endpoint", "context_info"} if mode == "open" else
             {"pcm_path", "start_sample", "end_sample", "sample_rate"}
             if mode == "append" else set())
    if mode not in {"open", "append", "poll", "finish"} or set(value) != required | extra:
        raise ValueError("invalid_stream_request")
    if mode == "open":
        endpoint, context = value["endpoint"], value["context_info"]
        if (not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 4096
                or not isinstance(context, str) or len(context.encode()) > 48 * 1024):
            raise ValueError("invalid_stream_options")
        vibevoice_ws_url(endpoint)
    if mode == "append" and (
        not isinstance(value["pcm_path"], str) or not 1 <= len(value["pcm_path"]) <= 4096
        or any(type(value[k]) is not int for k in ("start_sample", "end_sample", "sample_rate"))
        or not 8000 <= value["sample_rate"] <= 192000
        or not 0 <= value["start_sample"] < value["end_sample"] <= MAX_SAMPLE
        or (value["end_sample"] - value["start_sample"]) * 4 > MAX_PCM_BYTES
    ):
        raise ValueError("invalid_stream_pcm")
    return value


def read_pcm(value: dict) -> bytes:
    expected = (value["end_sample"] - value["start_sample"]) * 4
    before = os.lstat(value["pcm_path"])
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("invalid_stream_pcm")
    fd = os.open(value["pcm_path"], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size != expected
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("invalid_stream_pcm")
        pcm = stream.read(expected + 1)
    if len(pcm) != expected:
        raise ValueError("invalid_stream_pcm")
    # The trusted owner validates PCM too; reject malformed descriptors independently.
    import struct

    if any(not math.isfinite(v) or abs(v) > 4 for (v,) in struct.iter_unpack("<f", pcm)):
        raise ValueError("invalid_stream_pcm")
    return pcm


class StreamLeaf:
    def __init__(self) -> None:
        self.socket = None
        self.receiver: asyncio.Task | None = None
        self.identity: tuple | None = None
        self.epoch: int | None = None
        self.sample_rate = 0
        self.chunk_seconds = 0.
        self.transmitted_samples = 0
        self.messages: deque[dict] = deque()
        self.batch_bytes = 0
        self.done = False
        self.ended = False
        self.failure = False
        self.transmit_lock = asyncio.Lock()

    async def receive(self) -> None:
        try:
            async for raw in self.socket:
                if not isinstance(raw, str):
                    raise ValueError("invalid_stream_message")
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get("error"):
                    raise ValueError("invalid_stream_message")
                if "done" in message and type(message["done"]) is not bool:
                    raise ValueError("invalid_stream_message")
                if message.get("done"):
                    self.done = True
                    return
                text = message.get("text", "")
                if not isinstance(text, str) or len(text) > 20_000:
                    raise ValueError("invalid_stream_message")
                text = text.strip()
                text.encode("utf-8")
                if not text:
                    continue
                async with self.transmit_lock:
                    if self.transmitted_samples == 0:
                        raise ValueError("stream_text_before_audio")
                    item = {"text": text, "audio_end_sample": self.transmitted_samples}
                size = len(json.dumps(item, ensure_ascii=True).encode())
                if (len(self.messages) >= MAX_BATCH_MESSAGES
                        or self.batch_bytes + size > MAX_BATCH_BYTES):
                    raise ValueError("stream_batch_limit")
                self.messages.append(item)
                self.batch_bytes += size
        except Exception:
            self.failure = True
        finally:
            if not self.done:
                self.failure = True

    async def handle(self, request: dict) -> dict:
        value = validate(request)
        start_sample = self.transmitted_samples
        identity = tuple(request[key] for key in ("session_id", "session_generation",
                                                  "source_language"))
        if value["mode"] == "open":
            if self.identity is not None:
                raise ValueError("stream_already_open")
            self.identity, self.epoch = identity, value["candidate_epoch"]
            endpoint = value["endpoint"].rstrip("/")
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                config = await bounded_json_request(client, "GET", f"{endpoint}/v1/config")
            rate, seconds = config.get("sample_rate"), config.get("chunk_seconds", 2.)
            if (type(rate) is not int or not 8000 <= rate <= 192000
                    or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds) or not .05 <= seconds <= 60):
                raise ValueError("invalid_stream_configuration")
            self.sample_rate, self.chunk_seconds = rate, float(seconds)
            self.socket = await NoRedirectConnect(
                vibevoice_ws_url(endpoint), proxy=None, max_size=MAX_PCM_BYTES,
                max_queue=1, write_limit=4096, open_timeout=10, close_timeout=1,
            )
            await self.socket.send(json.dumps({
                "context_info": value["context_info"] or None,
                "max_tokens": 256, "temperature": 0., "top_p": 1.,
            }))
            self.receiver = asyncio.create_task(self.receive())
        else:
            if identity != self.identity or value["candidate_epoch"] != self.epoch:
                raise ValueError("stale_stream_candidate")
            if value["mode"] == "append":
                if (self.ended or self.done or self.failure
                        or value["sample_rate"] != self.sample_rate
                        or value["start_sample"] != self.transmitted_samples):
                    raise ValueError("invalid_stream_interval")
                pcm = read_pcm(value)
                # A receiver may run during flow control. Observe the completed
                # transport ACK before assigning its received text a PCM bound.
                async with self.transmit_lock:
                    await self.socket.send(pcm)
                    self.transmitted_samples = value["end_sample"]
            elif value["mode"] == "finish" and not self.ended:
                self.ended = True
                await self.socket.send("end")
            # Poll is bounded independently of an idle sidecar. Finish is retried
            # by the Rust owner under its original absolute drain deadline.
            await asyncio.sleep(0)
        if self.failure:
            raise ValueError("stream_transport_failed")
        result = {
            "stream_protocol_version": STREAM_PROTOCOL_VERSION,
            "candidate_epoch": self.epoch, "sample_rate": self.sample_rate,
            "chunk_seconds": self.chunk_seconds, "start_sample": start_sample,
            "transmitted_samples": self.transmitted_samples,
            "messages": list(self.messages), "done": self.done,
        }
        self.messages.clear()
        self.batch_bytes = 0
        return result

    async def close(self) -> None:
        if self.receiver is not None:
            self.receiver.cancel()
            with suppress(asyncio.CancelledError):
                await self.receiver
        if self.socket is not None:
            with suppress(Exception):
                await self.socket.close()


def main(args: list[str] | None = None) -> None:
    if (sys.argv[1:] if args is None else args):
        raise SystemExit(2)
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), sys.stdout.fileno())
        os.dup2(silent.fileno(), sys.stderr.fileno())
    incoming: queue.Queue[dict] = queue.Queue(maxsize=1)

    def read_requests() -> None:
        pending = bytearray()
        try:
            while True:
                data = read_parent_pipe(4096)
                if not data:
                    os._exit(0)
                pending.extend(data)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    if len(line) + 1 > MAX_FRAME_BYTES:
                        os._exit(1)
                    incoming.put_nowait(json.loads(line))
                    pending = bytearray(rest)
                if len(pending) >= MAX_FRAME_BYTES:
                    os._exit(1)
        except Exception:
            os._exit(1)

    async def serve() -> None:
        leaf = StreamLeaf()
        try:
            while True:
                request = await asyncio.to_thread(incoming.get)
                identity = {"protocol_version": 1, "request_id": "", "session_id": "",
                            "session_generation": 0, "segment_id": "", "source_revision": 0}
                try:
                    validate(request)
                    identity = {key: request[key] for key in IDENTITY_KEYS}
                    async with asyncio.timeout(request["deadline_ms"] / 1000):
                        result = await leaf.handle(request)
                    reply = {**identity, "status": "ok", "error": None,
                             "result": json.dumps(result, ensure_ascii=True, allow_nan=False)}
                    frame = (json.dumps(reply, ensure_ascii=True, allow_nan=False) + "\n").encode()
                    if len(frame) > MAX_FRAME_BYTES:
                        raise ValueError("stream_reply_limit")
                    output.write(frame)
                except Exception:
                    output.write((json.dumps({**identity, "status": "error", "result": None,
                                              "error": "asr_stream_failed"}) + "\n").encode())
                    await leaf.close()
                    os._exit(1)
        finally:
            await leaf.close()

    threading.Thread(target=read_requests, daemon=True).start()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
