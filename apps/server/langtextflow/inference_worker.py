"""Opt-in Rust ASR prototype worker; immutable results, no session/event ownership.

Only a trusted local parent may choose the WAV/model paths. This is not an HTTP
endpoint or a replacement for the production caption runtime/frozen sidecar.
"""
from __future__ import annotations

import json
import os
import queue
import stat
import sys
import threading
import wave

from .asr.faster_whisper import FasterWhisperStreamingAsrEngine
from .benchmark import pcm16le_to_f32le
from .models import SessionContext, StartSessionRequest
from .worker_ipc import MAX_FRAME_BYTES, PROTOCOL_VERSION

IDENTITY_KEYS = (
    "protocol_version", "request_id", "session_id", "session_generation",
    "segment_id", "source_revision",
)
REQUEST_KEYS = {*IDENTITY_KEYS, "task_kind", "source_language", "target_language",
                "deadline_ms", "payload"}


def validate_request(request: object) -> dict:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError("invalid_request")
    if (request["protocol_version"] != PROTOCOL_VERSION
            or type(request["protocol_version"]) is not int
            or request["task_kind"] != "asr" or request["target_language"] is not None
            or request["source_language"] not in {"ko", "en", "ja"}
            or type(request["deadline_ms"]) is not int
            or not 1 <= request["deadline_ms"] <= 120_000):
        raise ValueError("unsupported_request")
    for key in ("request_id", "session_id", "segment_id"):
        if not isinstance(request[key], str) or not 1 <= len(request[key]) <= 128:
            raise ValueError("invalid_identity")
    for key in ("session_generation", "source_revision"):
        if type(request[key]) is not int or not 1 <= request[key] <= 2**64 - 1:
            raise ValueError("invalid_identity")
    if not isinstance(request["payload"], str):
        raise ValueError("invalid_payload")
    chunk = json.loads(request["payload"])
    if not isinstance(chunk, dict) or set(chunk) != {
        "wav_path", "start_sample", "end_sample", "sample_rate",
    }:
        raise ValueError("invalid_audio_descriptor")
    if (not isinstance(chunk["wav_path"], str) or not 1 <= len(chunk["wav_path"]) <= 4096
            or type(chunk["sample_rate"]) is not int or chunk["sample_rate"] != 16_000
            or any(type(chunk[key]) is not int for key in ("start_sample", "end_sample"))
            or not 0 <= chunk["start_sample"] < chunk["end_sample"] <= (2**53) // 1000
            or chunk["end_sample"] - chunk["start_sample"] > 120 * 16_000):
        raise ValueError("invalid_audio_descriptor")
    return chunk


def read_pcm(chunk: dict) -> bytes:
    # Bound file size and each read independently; no raw audio crosses JSONL.
    fd = os.open(chunk["wav_path"], os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 128 * 1024 * 1024:
            raise ValueError("invalid_audio_file")
        with wave.open(stream, "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16_000):
                raise ValueError("invalid_audio_format")
            if chunk["end_sample"] > audio.getnframes():
                raise ValueError("audio_interval_out_of_bounds")
            audio.setpos(chunk["start_sample"])
            count = chunk["end_sample"] - chunk["start_sample"]
            raw = audio.readframes(count)
            if len(raw) != count * 2:
                raise ValueError("truncated_audio")
    return pcm16le_to_f32le(raw)


def infer(engine: FasterWhisperStreamingAsrEngine, request: dict, chunk: dict) -> dict:
    pcm = read_pcm(chunk)
    if engine._model is None:
        engine._model = engine._load_model_sync()  # Shared offline resolver, never bootstrap.
    engine._audio_cursor_samples = chunk["start_sample"]
    decoded = engine._transcribe_chunk_sync(pcm, StartSessionRequest(
        engine="faster-whisper", source_language=request["source_language"],
        # The shared request model requires a target even for ASR-only decode;
        # no translator is created or invoked by this worker.
        target_languages=["ko" if request["source_language"] == "en" else "en"],
        context=SessionContext(title="ASR prototype"),
    ))
    return {
        "sample_count": chunk["end_sample"] - chunk["start_sample"], "sample_rate": 16_000,
        "segments": [{
            "start_seconds": item.start_seconds, "end_seconds": item.end_seconds,
            "text": item.text, "estimated_timestamps": item.estimated_timestamps,
        } for item in decoded],
    }


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    if len(args) != 3 or not args[2].isdigit() or not 1 <= int(args[2]) <= 16:
        raise SystemExit(2)
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), sys.stdout.fileno())
        os.dup2(silent.fileno(), sys.stderr.fileno())
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    incoming: queue.Queue[dict] = queue.Queue(maxsize=1)

    def read_requests() -> None:
        # Raw FD reads avoid a buffered-stdin lock during terminal native errors.
        pending = bytearray()
        try:
            while True:
                data = os.read(0, 4096)
                if not data:
                    os._exit(0)  # Parent EOF also terminates blocked load/inference.
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

    async def unused_publish(_event):
        raise RuntimeError("worker must not publish caption events")

    engine = FasterWhisperStreamingAsrEngine(
        unused_publish, model=args[0], cache_dir=args[1], cpu_threads=int(args[2]),
        device="cpu", compute_type="int8", allow_download=False,
    )
    threading.Thread(target=read_requests, daemon=True).start()
    while True:
        request = incoming.get()
        identity = {"protocol_version": PROTOCOL_VERSION, "request_id": "", "session_id": "",
                    "session_generation": 0, "segment_id": "", "source_revision": 0}
        try:
            chunk = validate_request(request)
            identity = {key: request[key] for key in IDENTITY_KEYS}
            result = infer(engine, request, chunk)
            reply = {**identity, "status": "ok",
                     "result": json.dumps(result, ensure_ascii=False), "error": None}
            frame = (json.dumps(reply, ensure_ascii=False, allow_nan=False) + "\n").encode()
            if len(frame) > MAX_FRAME_BYTES:
                raise ValueError("result_too_large")
            output.write(frame)
        except Exception:
            # No paths, model IDs, endpoint strings, source text or tracebacks.
            output.write((json.dumps({**identity, "status": "error", "result": None,
                                      "error": "asr_worker_failed"}) + "\n").encode())
            os._exit(1)


if __name__ == "__main__":
    main()
