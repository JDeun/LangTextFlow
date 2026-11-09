"""Immutable correction leaves. Rust owns arbitration, deadlines and publication."""
from __future__ import annotations

import asyncio
import json
import math
import os
import queue
import sys
import threading

from .correction import DeterministicCorrector
from .llm_correction import CorrectionError, OllamaConstrainedCorrector
from .models import MAX_TRANSCRIPT_TEXT_CHARS, SessionContext
from .parent_pipe import read_parent_pipe
from .worker_ipc import IDENTITY_KEYS, MAX_FRAME_BYTES, validate_inference_request


def validate_request(request: object) -> tuple[str, str, SessionContext, dict]:
    validate_inference_request(request, "correction")
    payload = json.loads(request["payload"])
    if (not isinstance(payload, dict) or set(payload) != {"mode", "text", "context", "options"}
            or not isinstance(payload["mode"], str)
            or payload["mode"] not in {"prepare", "deterministic", "llm"}
            or not isinstance(payload["text"], str)
            or len(payload["text"]) > MAX_TRANSCRIPT_TEXT_CHARS
            or (payload["mode"] != "prepare" and not payload["text"].strip())
            or not isinstance(payload["context"], dict)
            or not set(payload["context"]) <= {
                "title", "presenter", "description", "reference_text",
                "preset", "hotwords", "glossary",
            } or len(json.dumps(payload["context"], ensure_ascii=False).encode()) > 48 * 1024):
        raise ValueError("invalid_correction_payload")
    options = payload["options"]
    if (not isinstance(options, dict)
            or set(options) != {"provider", "url", "model", "timeout_seconds"}
            or not isinstance(options["provider"], str)
            or options["provider"] not in {"none", "ollama"}
            or not isinstance(options["url"], str) or len(options["url"]) > 4096
            or not isinstance(options["model"], str) or not 1 <= len(options["model"]) <= 200
            or type(options["timeout_seconds"]) not in {int, float}
            or not math.isfinite(options["timeout_seconds"])
            or not .1 <= options["timeout_seconds"] <= 120):
        raise ValueError("invalid_correction_options")
    context = SessionContext.model_validate(payload["context"])
    context.glossary = [entry for entry in context.glossary if entry.enabled
                        and entry.source_language == request["source_language"]
                        and entry.applies_to(context.preset)]
    return payload["mode"], payload["text"], context, options


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
                    os._exit(0)
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
    deterministic = DeterministicCorrector()
    corrector = None
    configured = None
    prepared = False
    with asyncio.Runner() as runner:
        while True:
            request, mode, text, context, options = incoming.get()
            identity = {key: request[key] for key in IDENTITY_KEYS}
            error = None
            result = None
            try:
                if configured is not None and options != configured:
                    raise ValueError("changed_correction_configuration")
                configured = options
                if mode == "deterministic":
                    result = deterministic.correct(
                        text, context, source_language=request["source_language"],
                    )
                elif options["provider"] != "ollama":
                    raise CorrectionError("correction unavailable")
                elif mode == "prepare":
                    prepared = False
                    corrector = OllamaConstrainedCorrector(
                        base_url=options["url"], model=options["model"],
                        request_timeout_seconds=options["timeout_seconds"],
                    )
                    runner.run(corrector.prepare())
                    prepared = True
                    result = "ready"
                elif prepared and corrector is not None:
                    result = runner.run(corrector.correct(
                        text, source_language=request["source_language"], context=context,
                    ))
                else:
                    raise CorrectionError("correction unavailable")
            except CorrectionError:
                error = ("correction_provider_unavailable" if mode == "prepare"
                         else "correction_rejected")
            except Exception:
                error = "correction_worker_failed"
            reply = {**identity, "status": "error" if error else "ok",
                     "result": result, "error": error}
            frame = (json.dumps(reply, ensure_ascii=False, allow_nan=False) + "\n").encode()
            if len(frame) > MAX_FRAME_BYTES:
                os._exit(1)
            output.write(frame)
            if error == "correction_worker_failed":
                os._exit(1)


if __name__ == "__main__":
    main()
