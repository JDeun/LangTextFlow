"""Immutable HTTP/demo translation leaves; Rust owns deadlines and publication."""
from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import sys
import threading

from .models import MAX_TRANSCRIPT_TEXT_CHARS, SessionContext
from .parent_pipe import read_parent_pipe
from .translation import TranslationError
from .translation.factory import create_translator
from .worker_ipc import IDENTITY_KEYS, MAX_FRAME_BYTES, validate_inference_request


def validate_request(request: object) -> tuple[str, str, SessionContext, dict]:
    validate_inference_request(request, "translation")
    payload = json.loads(request["payload"])
    if (not isinstance(payload, dict) or set(payload) != {"mode", "text", "context", "options"}
            or payload["mode"] not in {"prepare", "translate"}
            or not isinstance(payload["text"], str)
            or len(payload["text"]) > MAX_TRANSCRIPT_TEXT_CHARS
            or (payload["mode"] != "prepare" and not payload["text"].strip())
            or not isinstance(payload["context"], dict)
            or not set(payload["context"]) <= {
                "title", "presenter", "preset", "glossary", "reference_text",
            } or len(json.dumps(payload["context"], ensure_ascii=False).encode()) > 64 * 1024):
        raise ValueError("invalid_translation_payload")
    options = payload["options"]
    if (not isinstance(options, dict)
            or set(options) != {"provider", "url", "model", "api_key", "timeout_seconds"}
            or options["provider"] not in {"demo", "ollama", "openai-compatible"}
            or not isinstance(options["url"], str) or len(options["url"]) > 4096
            or not isinstance(options["model"], str) or not 1 <= len(options["model"]) <= 200
            or not isinstance(options["api_key"], str) or len(options["api_key"]) > 8192
            or type(options["timeout_seconds"]) not in {int, float}
            or not math.isfinite(options["timeout_seconds"])
            or not .1 <= options["timeout_seconds"] <= 600):
        raise ValueError("invalid_translation_options")
    return (payload["mode"], payload["text"],
            SessionContext.model_validate(payload["context"]), options)


def main(args: list[str] | None = None) -> None:
    if (sys.argv[1:] if args is None else args):
        raise SystemExit(2)
    output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), sys.stdout.fileno())
        os.dup2(silent.fileno(), sys.stderr.fileno())
    incoming: queue.Queue = queue.Queue(maxsize=1)

    def read_requests() -> None:
        pending = bytearray()
        try:
            while True:
                data = read_parent_pipe(4096)
                if not data:
                    os._exit(0)  # EOF also interrupts a blocked HTTP exchange.
                pending.extend(data)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    if len(line) + 1 > MAX_FRAME_BYTES:
                        os._exit(1)
                    request = json.loads(line)
                    incoming.put_nowait((request, *validate_request(request)))
                    pending = bytearray(rest)
                if len(pending) >= MAX_FRAME_BYTES:
                    os._exit(1)
        except Exception:
            os._exit(1)

    threading.Thread(target=read_requests, daemon=True).start()
    configured = None
    provider = None
    prepared = False
    with asyncio.Runner() as runner:
        while True:
            request, mode, text, context, options = incoming.get()
            identity = {key: request[key] for key in IDENTITY_KEYS}
            result = error = None
            try:
                if configured is not None and options != configured:
                    raise ValueError("changed_translation_configuration")
                configured = options
                if provider is None:
                    provider = create_translator(options)
                if mode == "prepare":
                    prepared = False
                    runner.run(provider.prepare())
                    prepared = True
                    result = "ready"
                elif not prepared:
                    raise TranslationError("translation unavailable")
                else:
                    result = runner.run(provider.translate(
                        text, source_language=request["source_language"],
                        target_language=request["target_language"], context=context,
                    )).strip()
                    if not result or len(result) > 20_000:
                        raise TranslationError("invalid translation result")
            except TranslationError:
                error = ("translation_provider_unavailable" if mode == "prepare"
                         else "translation_rejected")
                result = None
            except Exception:
                error, result = "translation_worker_failed", None
            reply = {**identity, "status": "error" if error else "ok",
                     "result": result, "error": error}
            frame = (json.dumps(reply, ensure_ascii=False, allow_nan=False) + "\n").encode()
            if len(frame) > MAX_FRAME_BYTES:
                os._exit(1)
            output.write(frame)
            if error == "translation_worker_failed":
                os._exit(1)


if __name__ == "__main__":
    main()
