"""Real loopback providers receive synthetic content; environment proxies receive none."""

import asyncio
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import websockets

from langtextflow.asr import vibevoice
from langtextflow.llm_correction import OllamaConstrainedCorrector
from langtextflow.models import SessionContext, StartSessionRequest
from langtextflow.translation.ollama import OllamaTranslator
from langtextflow.translation.openai_compatible import OpenAICompatibleTranslator

MARKER = "SYNTHETIC_PRIVATE_PAYLOAD"


@pytest.fixture
def destinations(monkeypatch):
    calls = {"endpoint": [], "proxy": []}
    servers, threads, urls = [], [], {}

    def handler(destination):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                calls[destination].append((self.path, dict(self.headers), None))
                self.respond({"models": [{"name": "synthetic"}], "data": [{"id": "synthetic"}],
                              "sample_rate": 16000, "chunk_seconds": .1})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls[destination].append((self.path, dict(self.headers), body))
                text = (json.dumps({"corrected_text": MARKER})
                        if body.get("format") == "json" else "synthetic caption")
                self.respond({"message": {"content": text},
                              "choices": [{"message": {"content": text}}]})

            def do_CONNECT(self):
                calls[destination].append((self.path, dict(self.headers), None))
                self.send_error(502)  # A mistaken proxy route fails without forwarding data.
        return Handler

    try:
        for destination in calls:
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(destination))
            servers.append(server)
            thread = threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=.01))
            thread.start()
            threads.append(thread)
            urls[destination] = f"http://127.0.0.1:{server.server_address[1]}"
        for name in ("http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy"):
            monkeypatch.setenv(name, urls["proxy"])
            monkeypatch.setenv(name.upper(), urls["proxy"])
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        yield calls, urls
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
        assert all(not thread.is_alive() for thread in threads)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ollama-mt", "openai-mt", "correction"])
async def test_actual_model_calls_and_credentials_ignore_environment_proxy(destinations, kind):
    calls, urls = destinations
    context = SessionContext(title=MARKER)
    if kind == "openai-mt":
        provider = OpenAICompatibleTranslator(
            base_url=urls["endpoint"] + "/v1", model="synthetic", api_key="SYNTHETIC_API_KEY",
        )
    elif kind == "ollama-mt":
        provider = OllamaTranslator(base_url=urls["endpoint"], model="synthetic")
    else:
        provider = OllamaConstrainedCorrector(base_url=urls["endpoint"], model="synthetic")
    await provider.prepare()
    if kind == "correction":
        assert await provider.correct(MARKER, source_language="en", context=context) == MARKER
    else:
        assert await provider.translate(
            MARKER, source_language="en", target_language="ko", context=context,
        ) == "synthetic caption"
    assert calls["proxy"] == []
    assert len(calls["endpoint"]) == 2  # Actual availability request and model POST.
    _, headers, body = calls["endpoint"][-1]
    assert MARKER in body["messages"][1]["content"]
    if kind == "openai-mt":
        assert all(item[1]["Authorization"] == "Bearer SYNTHETIC_API_KEY"
                   for item in calls["endpoint"])
    else:
        assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_real_asr_websocket_sends_pcm_only_to_selected_sidecar(destinations, monkeypatch):
    calls, urls = destinations
    received, messages = asyncio.Event(), []

    async def sidecar(socket):
        async for message in socket:
            messages.append(message)
            if isinstance(message, bytes):
                received.set()
            elif message == "end":
                await socket.send(json.dumps({"done": True}))
                return

    async def publish(_event):
        pass

    async with websockets.serve(sidecar, "127.0.0.1", 0) as server:
        address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/stream"
        monkeypatch.setattr(vibevoice, "vibevoice_ws_url", lambda _: address)
        engine = vibevoice.VibeVoiceStreamingAsrEngine(publish, base_url=urls["endpoint"])
        pcm = struct.pack("<f", .25) * 160
        try:
            await engine.start(StartSessionRequest(
                engine="vibevoice", context=SessionContext(title=MARKER),
            ))
            await engine.feed_audio(pcm)
            await asyncio.wait_for(received.wait(), 2)
        finally:
            await engine.stop()
    assert calls["proxy"] == []
    assert len(calls["endpoint"]) == 1 and calls["endpoint"][0][0] == "/v1/config"
    assert MARKER in json.loads(messages[0])["context_info"]
    assert pcm in messages
