"""Experimental stdio JSONL worker for the Rust IPC contract.

This is a deterministic protocol fixture, NOT a production ASR/MT backend.
The parent must supervise deadlines, cancellation and worker restart.
"""
from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = 1
TASK_KINDS = {"asr", "translation", "correction"}


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
        return {**identity, "status": "error", "result": None, "error": "unsupported_protocol_version"}
    if request.get("task_kind") not in TASK_KINDS:
        return {**identity, "status": "error", "result": None, "error": "unsupported_task_kind"}
    if not isinstance(request.get("payload"), str):
        return {**identity, "status": "error", "result": None, "error": "invalid_payload"}
    # Echo is intentional: this fixture only exercises request correlation.
    return {**identity, "status": "ok", "result": request["payload"], "error": None}


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            reply = handle_request(request)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            reply = {"protocol_version": PROTOCOL_VERSION, "request_id": "", "session_id": "", "session_generation": 0, "segment_id": "", "source_revision": 0, "status": "error", "result": None, "error": str(exc)}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
