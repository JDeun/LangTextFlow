"""Loopback-only deterministic provider for browser summary integration tests."""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_MODELS = {"synthetic-e2e": 0, "synthetic-offline-e2e": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass  # Never log transcript request bodies from the test provider.

    def reply(self, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path == "/api/tags":
            self.reply({"models": [{"name": name} for name in _MODELS]})
        elif self.path == "/fixture/calls":
            self.reply(_MODELS)
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError("invalid test request size")
            request = json.loads(self.rfile.read(length))
            model = request["model"]
            if model not in _MODELS:
                raise ValueError("only synthetic fixture models are supported")
            evidence = json.loads(request["messages"][-1]["content"])["transcript_evidence"]
            marker = re.search(r"\[[^\]\n]+ at \d+(?:\.\d+)?s\]", evidence)
            if marker is None:
                raise ValueError("source citation required")
        except (ValueError, KeyError, TypeError, IndexError):
            self.send_error(400)
            return
        _MODELS[model] += 1
        title = "Offline summary" if model == "synthetic-offline-e2e" else "Synthetic summary"
        self.reply({"message": {"content": f"{title} {marker.group()}"}})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Path(sys.argv[1]).write_text(str(server.server_port) + "\n", encoding="utf-8")
    server.serve_forever()
