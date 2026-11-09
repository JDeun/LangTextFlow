"""Private native owner; default runtime IPC or explicit Rust v1 inference mode."""
from __future__ import annotations

import json
import os
import queue
import sys
import threading

from ..models import MAX_TRANSCRIPT_TEXT_CHARS, SessionContext
from ..parent_pipe import read_parent_pipe
from ..worker_ipc import IDENTITY_KEYS, MAX_FRAME_BYTES, validate_inference_request
from .base import TranslationRejected
from .m2m100_ct2 import (
    IPC_LIMIT,
    SUPPORTED_LANGUAGES,
    M2M100CT2Translator,
    missing_m2m100_cache_files,
)


def validate_rust_request(request: object) -> tuple[str, SessionContext]:
    validate_inference_request(request, "translation")
    if (request["source_language"] not in SUPPORTED_LANGUAGES
            or request["target_language"] not in SUPPORTED_LANGUAGES):
        raise ValueError("unsupported_local_translation_pair")
    payload = json.loads(request["payload"])
    if (not isinstance(payload, dict)
            or set(payload) not in ({"text", "context"}, {"text", "context", "mode"})
            or ("mode" in payload and payload["mode"] != "prepare")
            or not isinstance(payload["text"], str)
            or (payload.get("mode") != "prepare" and not payload["text"].strip())
            or len(payload["text"]) > MAX_TRANSCRIPT_TEXT_CHARS
            or not isinstance(payload["context"], dict)
            or not set(payload["context"]) <= {"preset", "glossary"}):
        raise ValueError("invalid_translation_payload")
    return payload["text"], SessionContext.model_validate(payload["context"])


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    rust_protocol = bool(args and args[0] == "--rust-protocol")
    if rust_protocol:
        args = args[1:]
    if len(args) != 3 or not args[2].isdigit() or not 1 <= int(args[2]) <= 16:
        raise SystemExit(2)
    # Keep native libraries' stdout/logging out of the bounded JSON channel.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), sys.stdout.fileno())
        os.dup2(silent.fileno(), sys.stderr.fileno())
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", USE_TORCH="0")
    incoming: queue.Queue = queue.Queue(maxsize=1 if rust_protocol else 4)

    def read_requests() -> None:
        pending = bytearray()
        try:
            while True:
                data = read_parent_pipe(4096)
                if not data:
                    os._exit(0 if rust_protocol else 1)
                pending.extend(data)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    limit = MAX_FRAME_BYTES if rust_protocol else IPC_LIMIT - 1
                    if len(line) + 1 > limit:
                        os._exit(1)
                    request = json.loads(line)
                    if rust_protocol:
                        text, context = validate_rust_request(request)
                        incoming.put_nowait((request, text, context))
                    else:
                        if (not isinstance(request, dict)
                                or set(request) != {"id", "text", "source", "target"}
                                or type(request["id"]) is not int or request["id"] < 1
                                or not isinstance(request["text"], str)
                                or not 1 <= len(request["text"].strip())
                                <= MAX_TRANSCRIPT_TEXT_CHARS
                                or request["source"] not in SUPPORTED_LANGUAGES
                                or request["target"] not in SUPPORTED_LANGUAGES
                                or request["source"] == request["target"]):
                            os._exit(1)
                        incoming.put_nowait(request)
                    pending = bytearray(rest)
                if len(pending) >= (MAX_FRAME_BYTES if rust_protocol else IPC_LIMIT - 1):
                    os._exit(1)
        except Exception:
            os._exit(1)

    # Reads EOF even while native model initialization/inference is blocked.
    threading.Thread(target=read_requests, daemon=True).start()
    identity = None
    try:
        provider = M2M100CT2Translator(
            model_dir=args[0], tokenizer_dir=args[1], cpu_threads=int(args[2]),
        )
        prepared = None
        if not rust_protocol:
            prepared = provider._prepare_sync()
            print(json.dumps({"ready": True}), file=output, flush=True)
        while True:
            admitted = incoming.get()
            if rust_protocol:
                request, text, context = admitted
                identity = {key: request[key] for key in IDENTITY_KEYS}
                source, target = request["source_language"], request["target_language"]
            else:
                request = admitted
                text, source, target = request["text"], request["source"], request["target"]
            if prepared is None:
                if rust_protocol and missing_m2m100_cache_files(
                    provider.model_dir, provider.tokenizer_dir,
                ):
                    raise ValueError("local translation cache is incomplete")
                prepared = provider._prepare_sync()
            try:
                if rust_protocol and json.loads(request["payload"]).get("mode") == "prepare":
                    print(json.dumps({**identity, "status": "ok", "result": "ready",
                                      "error": None}), file=output, flush=True)
                    continue
                candidate = provider._translate_sync(text, source, target, *prepared)
                if rust_protocol:
                    provider._check_glossary(candidate, text, source, target, context)
            except TranslationRejected:
                if rust_protocol:
                    response = {**identity, "status": "error", "result": None,
                                "error": "translation_rejected"}
                else:
                    response = {"id": request["id"], "error": "translation_rejected"}
                print(json.dumps(response), file=output, flush=True)
                continue
            if rust_protocol:
                response = json.dumps({**identity, "status": "ok", "result": candidate,
                                       "error": None}, ensure_ascii=False)
            else:
                response = json.dumps({"id": request["id"], "text": candidate}, ensure_ascii=False)
            if len(response.encode("utf-8")) + 1 > (
                MAX_FRAME_BYTES if rust_protocol else IPC_LIMIT - 1
            ):
                raise ValueError("translation exceeds private IPC limit")
            print(response, file=output, flush=True)
    except Exception:
        try:
            if rust_protocol and identity is not None:
                print(json.dumps({**identity, "status": "error", "result": None,
                                  "error": "translation_worker_failed"}), file=output, flush=True)
        finally:
            os._exit(1)  # Native finalization must not hold cancellation or the parent lease.


if __name__ == "__main__":
    main()
