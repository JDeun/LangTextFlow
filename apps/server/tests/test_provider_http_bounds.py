"""Exercise actual provider streaming: envelope bytes count even for short model text."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from langtextflow.asr.base import AsrEngineError
from langtextflow.asr.vibevoice import VibeVoiceStreamingAsrEngine
from langtextflow.config import Settings
from langtextflow.llm_correction import CorrectionError, OllamaConstrainedCorrector
from langtextflow.models import CaptionStage, SessionContext, StartSessionRequest, TranscriptEvent
from langtextflow.pipeline import CaptionPipeline
from langtextflow.post_event_summary import generate_summary
from langtextflow.preflight import (
    CheckStatus,
    _ollama_checks,
    _openai_compatible_checks,
    _vibevoice_check,
)
from langtextflow.provider_http import MAX_PROVIDER_RESPONSE_BYTES, bounded_json_request
from langtextflow.translation.base import TranslationError
from langtextflow.translation.ollama import OllamaTranslator
from langtextflow.translation.openai_compatible import OpenAICompatibleTranslator
from langtextflow.vibevoice_lifecycle import VibeVoiceLifecycleManager


class EnvelopeBody(httpx.AsyncByteStream):
    def __init__(self, payload):
        self.prefix = json.dumps(payload).encode()[:-1] + b',"ignored_padding":"'
        self.read_bytes = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in [self.prefix, *([b"x" * 65536] * 128), b'"}']:
            self.read_bytes += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def mock_provider(monkeypatch, respond):
    native_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options:
        native_client(transport=httpx.MockTransport(respond), **options))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    "ollama-translate", "compatible-translate", "correction", "ollama-summary",
    "compatible-summary", "ollama-models", "compatible-models", "correction-models",
])
@pytest.mark.parametrize("declared", [None, "1", str(MAX_PROVIDER_RESPONSE_BYTES + 1)])
async def test_every_llm_http_path_bounds_entire_envelope(monkeypatch, operation, declared):
    body = EnvelopeBody({
        "message": {"content": "Valid translated text"},
        "choices": [{"message": {"content": "Valid translated text"}}],
        "models": [{"name": "fixture"}], "data": [{"id": "fixture"}],
    })

    def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        headers = {"content-length": declared} if declared is not None else {}
        return httpx.Response(200, headers=headers, stream=body)

    mock_provider(monkeypatch, respond)
    ollama = OllamaTranslator(base_url="http://unused", model="fixture")
    compatible = OpenAICompatibleTranslator(base_url="http://unused/v1", model="fixture")
    corrector = OllamaConstrainedCorrector(base_url="http://unused", model="fixture")
    expected = (CorrectionError if operation.startswith("correction") else
                ValueError if operation.endswith("summary") else TranslationError)
    with pytest.raises(expected, match="safety byte limit"):
        if operation.endswith("models"):
            await {"ollama-models": ollama, "compatible-models": compatible,
                   "correction-models": corrector}[operation].prepare()
        elif operation == "correction":
            await corrector.correct("원문", source_language="ko", context=SessionContext())
        elif operation.endswith("summary"):
            await generate_summary(
                "[caption-1 at 0s] {} source", language="en",
                provider="ollama" if operation.startswith("ollama") else "openai-compatible",
                model="fixture", settings=Settings(), allow_external_provider=True,
            )
        else:
            await (ollama if operation.startswith("ollama") else compatible).translate(
                "원문", source_language="ko", target_language="en", context=SessionContext(),
            )
    assert body.closed
    if declared not in {None, "1"}:
        assert body.read_bytes == 0
    else:
        # Transport reads are bounded too; do not drain the eight-MiB envelope.
        assert MAX_PROVIDER_RESPONSE_BYTES < body.read_bytes <= MAX_PROVIDER_RESPONSE_BYTES + 65536


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    "preflight-ollama", "preflight-compatible", "preflight-vibevoice",
    "asr-config", "health-config",
])
async def test_metadata_checks_bound_bytes_and_report_unavailable(monkeypatch, operation):
    body = EnvelopeBody({"sample_rate": 16000, "models": [{"name": "fixture"}]})
    mock_provider(monkeypatch, lambda _: httpx.Response(200, stream=body))
    if operation == "preflight-ollama":
        checks = await _ollama_checks(Settings(), "fixture")
        assert all(check.status is CheckStatus.MISSING for check in checks)
    elif operation == "preflight-compatible":
        checks = await _openai_compatible_checks(Settings(), "fixture")
        assert all(check.status is CheckStatus.MISSING for check in checks)
    elif operation == "preflight-vibevoice":
        assert (await _vibevoice_check(Settings())).status is CheckStatus.MISSING
    elif operation == "health-config":
        assert not await VibeVoiceLifecycleManager(Settings())._probe_health()
    else:
        async def publish(event):
            pytest.fail("invalid ASR config must not publish")

        engine = VibeVoiceStreamingAsrEngine(publish, base_url="http://unused")
        with pytest.raises(AsrEngineError, match="safety byte limit"):
            await engine.start(StartSessionRequest())
        assert not engine.running
    assert body.closed and MAX_PROVIDER_RESPONSE_BYTES < body.read_bytes < 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,error", [
    ({"content-encoding": "gzip"}, "identity content encoding"),
    ({"content-encoding": "br"}, "identity content encoding"),
    ({"content-length": "-1"}, "invalid content length"),
    ({"content-length": "private-secret"}, "invalid content length"),
])
async def test_unsafe_headers_fail_before_read_and_close_stream(headers, error):
    body = EnvelopeBody({"content": "private-response"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers=headers, stream=body),
    )) as client:
        with pytest.raises(ValueError, match=error) as failure:
            await bounded_json_request(client, "GET", "http://unused")
    assert body.closed and body.read_bytes == 0
    assert "private" not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [307, 500])
async def test_http_errors_do_not_follow_redirects_read_body_or_expose_url(status):
    body = EnvelopeBody({"private": "private-response"})
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://other.invalid"}, stream=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True,
    ) as client:
        with pytest.raises(ValueError) as failure:
            await bounded_json_request(client, "GET", "https://user:secret@unused/?key=private-key")
    assert str(failure.value) == f"provider returned HTTP {status}"
    assert len(requests) == 1 and body.closed and body.read_bytes == 0


@pytest.mark.asyncio
async def test_network_error_details_are_sanitized():
    def respond(request):
        raise httpx.ReadTimeout(f"private-provider-detail {request.url}", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.TimeoutException) as failure:
            await bounded_json_request(client, "GET", "https://unused/?key=private-key")
    assert str(failure.value) == "provider HTTP request timed out"


@pytest.mark.asyncio
async def test_exact_byte_limit_and_unicode_json_are_accepted():
    prefix = b'{"content":"ok","padding":"'
    body = prefix + b"x" * (MAX_PROVIDER_RESPONSE_BYTES - len(prefix) - 2) + b'"}'
    assert len(body) == MAX_PROVIDER_RESPONSE_BYTES
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=body),
    )) as client:
        assert (await bounded_json_request(client, "GET", "http://unused"))["content"] == "ok"
    escaped = json.dumps({"content": "😀" * 50_000}).encode()
    assert len(escaped) < MAX_PROVIDER_RESPONSE_BYTES
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=escaped),
    )) as client:
        payload = await bounded_json_request(client, "GET", "http://unused")
        assert len(payload["content"]) == 50_000


@pytest.mark.asyncio
async def test_cancellation_closes_stream_without_draining():
    entered = asyncio.Event()
    closed = asyncio.Event()

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"{}"

        async def aclose(self):
            closed.set()

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=SlowBody()),
    )) as client:
        task = asyncio.create_task(bounded_json_request(client, "GET", "http://unused"))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()


@pytest.mark.asyncio
async def test_oversized_real_correction_and_translation_preserve_raw_source(monkeypatch):
    bodies = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "fixture"}]})
        body = EnvelopeBody({"message": {"content": "Valid translated text"}})
        bodies.append(body)
        return httpx.Response(200, stream=body)

    mock_provider(monkeypatch, respond)
    committed = asyncio.Event()
    events = []

    async def publish(event):
        events.append(event)
        if event.committed:
            committed.set()

    pipeline = CaptionPipeline(publish, Settings())
    monkeypatch.setattr(pipeline, "_build_corrector", lambda _: OllamaConstrainedCorrector(
        base_url="http://unused", model="fixture",
    ))
    monkeypatch.setattr(pipeline, "_build_translator", lambda _: OllamaTranslator(
        base_url="http://unused", model="fixture",
    ))
    source = "오늘 요한 보금 3장 말씀입니다"
    await pipeline.start(StartSessionRequest(
        source_language="ko", target_languages=["en"], correction_provider="ollama",
        translation_provider="ollama", context=SessionContext(preset="church"),
    ))
    try:
        await pipeline.ingest(TranscriptEvent(
            segment_id="source", stage=CaptionStage.STABLE, version=1,
            source_language="ko", text=source, start_ms=0, end_ms=1000,
        ))
        await asyncio.wait_for(committed.wait(), 2)
    finally:
        await pipeline.stop()
    final = events[-1]
    assert final.committed and final.original_text == source
    assert final.text == "오늘 요한복음 3장 말씀입니다"
    assert final.translations == {} and final.translation_states == {"en": "failed"}
    assert final.correction.method == "fallback" and not final.correction.llm_applied
    assert len(bodies) == 2 and all(body.closed for body in bodies)
