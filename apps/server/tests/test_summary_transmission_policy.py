"""Summary consent follows configured endpoint policy, including direct callers."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from langtextflow import post_event_summary
from langtextflow.config import Settings


@pytest.mark.parametrize("url,external", [
    ("http://localhost:11434", False), ("http://127.0.0.1:11434", False),
    ("https://[::1]:11434", False), ("https://ollama.example", True),
    ("http://localhost.example", True), ("file://localhost/models", True),
    ("http://[invalid", True),
])
def test_summary_endpoint_policy(url, external):
    assert post_event_summary.summary_external_providers(Settings(ollama_url=url)) == (
        ["openai-compatible", "ollama"] if external else ["openai-compatible"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
async def test_direct_summary_without_approval_never_opens_client(monkeypatch, provider):
    def forbidden(**kwargs):
        pytest.fail("unapproved transcript must not reach HTTP client")

    monkeypatch.setattr(post_event_summary.httpx, "AsyncClient", forbidden)
    with pytest.raises(ValueError, match="explicit opt-in"):
        await post_event_summary.generate_summary(
            "[caption-1 at 0s] {} private speech", language="ko", provider=provider,
            model="test", settings=Settings(ollama_url="https://remote.example"),
            allow_external_provider=False,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("redirect", [False, True])
async def test_ollama_approved_policy_and_no_redirect_following(monkeypatch, remote, redirect):
    calls = []
    url = "https://remote.example" if remote else "http://localhost:11434"
    native_client = httpx.AsyncClient

    def respond(request):
        calls.append(str(request.url))
        if redirect:
            return httpx.Response(307, headers={"Location": "https://other.example/api/chat"})
        return httpx.Response(200, json={"message": {"content": "summary [caption-1 at 0s]"}})

    monkeypatch.setattr(post_event_summary.httpx, "AsyncClient", lambda **kwargs: native_client(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    async def generate():
        return await post_event_summary.generate_summary(
            "[caption-1 at 0s] {} private speech", language="ko", provider="ollama",
            model="test", settings=Settings(ollama_url=url), allow_external_provider=remote,
        )

    if redirect:
        with pytest.raises(ValueError, match="HTTP 307"):
            await generate()
    else:
        assert "summary" in await generate()
    assert calls == [url + "/api/chat"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
async def test_real_http_summary_uses_only_consented_endpoint_not_environment_proxy(
    monkeypatch, provider,
):
    calls = {"endpoint": [], "proxy": []}
    servers, threads, urls = [], [], {}

    def handler(destination):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                calls[destination].append((self.path, json.loads(body)))
                text = f"{destination} summary [caption-1 at 0s]"
                response = json.dumps({
                    "message": {"content": text},
                    "choices": [{"message": {"content": text}}],
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        return Handler

    try:
        for destination in calls:
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(destination))
            servers.append(server)
            thread = threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=.01))
            thread.start()
            threads.append(thread)
            urls[destination] = f"http://127.0.0.1:{server.server_address[1]}"
        for name in (
            "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
        ):
            monkeypatch.setenv(name, urls["proxy"])
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        settings = Settings(
            ollama_url=urls["endpoint"], openai_compatible_url=urls["endpoint"] + "/v1",
        )
        result = await post_event_summary.generate_summary(
            "[caption-1 at 0s] {} SYNTHETIC_PROXY_BOUNDARY", language="en", provider=provider,
            model="synthetic", settings=settings,
            allow_external_provider=provider == "openai-compatible",
        )
        assert result == "endpoint summary [caption-1 at 0s]"
        assert calls["proxy"] == []
        assert len(calls["endpoint"]) == 1
        path, body = calls["endpoint"][0]
        assert path == ("/api/chat" if provider == "ollama" else "/v1/chat/completions")
        assert "SYNTHETIC_PROXY_BOUNDARY" in body["messages"][1]["content"]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
        assert all(not thread.is_alive() for thread in threads)
