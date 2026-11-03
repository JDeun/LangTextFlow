"""Summary consent follows configured endpoint policy, including direct callers."""
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
