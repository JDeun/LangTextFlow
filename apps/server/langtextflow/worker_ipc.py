"""Shared inference contract and standalone experimental stdio JSONL echo fixture.

The parent owns cancellation, deadlines and worker restart. Oversized control
frames terminate the fixture; speech/audio must never be sent on this channel.
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 128 * 1024  # Includes the newline; matches the Rust transport.
TASK_KINDS = {"asr", "translation", "correction"}
SUPPORTED_LANGUAGES = frozenset({"ko", "en", "ja", "zh", "es", "fr", "de", "it", "pt", "ru"})
IDENTITY_KEYS = (
    "protocol_version", "request_id", "session_id", "session_generation",
    "segment_id", "source_revision",
)
REQUEST_KEYS = {*IDENTITY_KEYS, "task_kind", "source_language", "target_language",
                "deadline_ms", "payload"}
INFERENCE_DEVICES = {"cpu", "cuda", "auto"}
INFERENCE_COMPUTE_TYPES = {
    "default", "auto", "int8", "int8_float32", "int8_float16", "int8_bfloat16",
    "int16", "float16", "float32", "bfloat16",
}


def validate_inference_options(options: object) -> dict:
    if (not isinstance(options, dict)
            or set(options) != {"protocol_version", "device", "compute_type"}
            or type(options["protocol_version"]) is not int or options["protocol_version"] != 1
            or not isinstance(options["device"], str) or options["device"] not in INFERENCE_DEVICES
            or not isinstance(options["compute_type"], str)
            or options["compute_type"] not in INFERENCE_COMPUTE_TYPES):
        raise ValueError("invalid_inference_options")
    return options


def validate_inference_request(
    request: object, task_kind: str, *, max_deadline_ms: int = 120_000,
) -> None:
    """Real workers accept the fixed v1 contract before touching native state."""
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError("invalid_request")
    if (type(request["protocol_version"]) is not int
            or request["protocol_version"] != PROTOCOL_VERSION
            or task_kind not in TASK_KINDS or request["task_kind"] != task_kind
            or not isinstance(request["source_language"], str)
            or request["source_language"] not in SUPPORTED_LANGUAGES
            or type(request["deadline_ms"]) is not int
            or not 1 <= request["deadline_ms"] <= max_deadline_ms):
        raise ValueError("unsupported_request")
    target = request["target_language"]
    if ((task_kind in {"asr", "correction"} and target is not None)
            or (task_kind == "translation" and (
                not isinstance(target, str) or target not in SUPPORTED_LANGUAGES
                or target == request["source_language"]))):
        raise ValueError("unsupported_request")
    for key in ("request_id", "session_id", "segment_id"):
        if not isinstance(request[key], str) or not 1 <= len(request[key]) <= 128:
            raise ValueError("invalid_identity")
    for key in ("session_generation", "source_revision"):
        if type(request[key]) is not int or not 1 <= request[key] <= 2**64 - 1:
            raise ValueError("invalid_identity")
    if not isinstance(request["payload"], str):
        raise ValueError("invalid_payload")


def error_reply(error: str) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "",
        "session_id": "",
        "session_generation": 0,
        "segment_id": "",
        "source_revision": 0,
        "status": "error",
        "result": None,
        "error": error,
    }


def handle_request(request: dict) -> dict:
    identity = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request.get("request_id", ""),
        "session_id": request.get("session_id", ""),
        "session_generation": request.get("session_generation", 0),
        "segment_id": request.get("segment_id", ""),
        "source_revision": request.get("source_revision", 0),
    }
    if request.get("protocol_version") != PROTOCOL_VERSION:
        return {
            **identity, "status": "error", "result": None,
            "error": "unsupported_protocol_version",
        }
    if request.get("task_kind") not in TASK_KINDS:
        return {**identity, "status": "error", "result": None, "error": "unsupported_task_kind"}
    if not isinstance(request.get("payload"), str):
        return {**identity, "status": "error", "result": None, "error": "invalid_payload"}
    # Echo only; payloads are never evaluated, executed, or passed to a model.
    return {**identity, "status": "ok", "result": request["payload"], "error": None}


def main() -> None:
    while line := sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1):
        if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
            reply = error_reply("frame_too_large_or_unterminated")
            terminal = True
        else:
            terminal = False
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                reply = handle_request(request)
            except (ValueError, TypeError, RecursionError) as exc:
                reply = error_reply(str(exc))
        try:
            frame = (json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        except UnicodeError:
            frame = (json.dumps(error_reply("invalid_utf8")) + "\n").encode()
        if len(frame) > MAX_FRAME_BYTES:
            frame = (json.dumps(error_reply("reply_frame_too_large")) + "\n").encode()
            terminal = True
        sys.stdout.buffer.write(frame)
        sys.stdout.buffer.flush()
        if terminal:
            return


if __name__ == "__main__":
    main()
