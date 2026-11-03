"""Private, killable local M2M100 CPU process; no application/server initialization."""
from __future__ import annotations

import json
import os
import queue
import sys
import threading

from ..models import MAX_TRANSCRIPT_TEXT_CHARS
from .m2m100_ct2 import IPC_LIMIT, SUPPORTED_LANGUAGES, M2M100CT2Translator


def main(args: list[str] | None = None) -> None:
    args = sys.argv[1:] if args is None else args
    if len(args) != 3 or not args[2].isdigit() or not 1 <= int(args[2]) <= 16:
        raise SystemExit(2)
    # Keep native libraries' stdout/logging out of the bounded JSON channel.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    with open(os.devnull, "w") as silent:
        os.dup2(silent.fileno(), sys.stdout.fileno())
        os.dup2(silent.fileno(), sys.stderr.fileno())
    requests: queue.Queue[dict] = queue.Queue(maxsize=4)

    def read_requests() -> None:
        try:
            while True:
                line = sys.stdin.buffer.readline(IPC_LIMIT)
                if not line or not line.endswith(b"\n") or len(line) >= IPC_LIMIT:
                    os._exit(1)
                request = json.loads(line)
                if (not isinstance(request, dict)
                        or set(request) != {"id", "text", "source", "target"}
                        or type(request["id"]) is not int or request["id"] < 1
                        or not isinstance(request["text"], str)
                        or not 1 <= len(request["text"].strip()) <= MAX_TRANSCRIPT_TEXT_CHARS
                        or request["source"] not in SUPPORTED_LANGUAGES
                        or request["target"] not in SUPPORTED_LANGUAGES
                        or request["source"] == request["target"]):
                    os._exit(1)
                requests.put_nowait(request)
        except Exception:
            os._exit(1)

    # Reads EOF even while native model initialization/inference is blocked.
    threading.Thread(target=read_requests, daemon=True).start()
    try:
        provider = M2M100CT2Translator(
            model_dir=args[0], tokenizer_dir=args[1], cpu_threads=int(args[2]),
        )
        tokenizer, engine = provider._prepare_sync()
        print(json.dumps({"ready": True}), file=output, flush=True)
        while True:
            request = requests.get()
            candidate = provider._translate_sync(
                request["text"], request["source"], request["target"], tokenizer, engine,
            )
            response = json.dumps({"id": request["id"], "text": candidate}, ensure_ascii=False)
            if len(response.encode("utf-8")) >= IPC_LIMIT - 1:
                raise ValueError("translation exceeds private IPC limit")
            print(response, file=output, flush=True)
    except Exception:
        # The daemon reader may own stdin's buffered lock during native failure.
        os._exit(1)


if __name__ == "__main__":
    main()
