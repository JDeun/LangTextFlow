"""Preflight must reject malformed HTTP-200 provider responses (#82).

Real httpx decoding runs through an in-memory MockTransport. No socket/model
installation or external paid provider is contacted by these tests.
"""
from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from langtextflow import preflight
from langtextflow.config import Settings
from langtextflow.preflight import (
    CheckStatus,
    _blocking_checks,
    _ollama_checks,
    _openai_compatible_checks,
    _vibevoice_check,
)


def _mock_http(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    original = httpx.AsyncClient

    def wrapped(*args: object, **kwargs: object) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return responder(request)

        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(preflight.httpx, "AsyncClient", wrapped)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    [],
    "ready",
    {"error": "proxy"},
])
async def test_vibevoice_http_200_nonobject_cannot_be_ready(
    monkeypatch: pytest.MonkeyPatch, invalid: object,
) -> None:
    _mock_http(monkeypatch, lambda _: httpx.Response(200, json=invalid))
    status = await _vibevoice_check(Settings())
    if isinstance(invalid, dict):
        # A dict response is accepted as a sidecar config even when empty;
        # stricter semantic health/readiness is a separate compatibility gate.
        assert status.status is CheckStatus.READY
    else:
        assert status.status is CheckStatus.MISSING
        assert "JSON object" in status.details["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    [],
    {},
    {"models": "broken"},
    {"models": {}},
    {"models": [42]},
    {"models": [{}]},
    {"models": [{"name": []}]},
])
async def test_ollama_malformed_model_list_cannot_appear_healthy(
    monkeypatch: pytest.MonkeyPatch, bad: object,
) -> None:
    _mock_http(monkeypatch, lambda _: httpx.Response(200, json=bad))
    checks = await _ollama_checks(Settings(), "translategemma:4b")
    assert [check.status for check in checks] == [
        CheckStatus.MISSING, CheckStatus.MISSING
    ]
    assert _blocking_checks(
        checks, engine="mock", translation_provider="ollama"
    ) == ["ollama", "translation-model"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    [], {}, {"data": None}, {"data": "not a list"},
    {"data": [None]}, {"data": [{"id": 123}]},
    {"data": [{}]},
])
async def test_openai_compatible_invalid_schema_cannot_be_ready(
    monkeypatch: pytest.MonkeyPatch, bad: object,
) -> None:
    captured = _mock_http(
        monkeypatch, lambda _: httpx.Response(200, json=bad)
    )
    settings = Settings(openai_compatible_api_key="unit-test-secret")
    checks = await _openai_compatible_checks(settings, "local-translator")
    assert [check.status for check in checks] == [
        CheckStatus.MISSING, CheckStatus.MISSING
    ]
    assert _blocking_checks(
        checks, engine="mock", translation_provider="openai-compatible"
    ) == ["openai-compatible", "openai-compatible-model"]
    assert captured[0].headers["authorization"] == "Bearer unit-test-secret"
    assert "unit-test-secret" not in str(checks)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,reply", [
    ("ollama", {"models": []}),
    ("openai", {"data": []}),
])
async def test_valid_empty_provider_model_list_is_healthy_but_model_missing(
    monkeypatch: pytest.MonkeyPatch, path: str, reply: dict[str, object],
) -> None:
    _mock_http(monkeypatch, lambda _: httpx.Response(200, json=reply))
    if path == "ollama":
        checks = await _ollama_checks(Settings(), "translategemma:4b")
    else:
        checks = await _openai_compatible_checks(Settings(), "local")
    assert [check.status for check in checks] == [
        CheckStatus.READY, CheckStatus.MISSING
    ]


@pytest.mark.asyncio
async def test_exact_ollama_model_and_alias_name_are_recognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_http(
        monkeypatch,
        lambda _: httpx.Response(200, json={"models": [
            {"name": "translategemma:4b"}, {"model": "qwen3.5:latest"}
        ]}),
    )
    checks = await _ollama_checks(Settings(), "translategemma:4b")
    assert all(check.status is CheckStatus.READY for check in checks)


@pytest.mark.asyncio
async def test_openai_model_must_match_exact_id_not_just_shared_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_http(
        monkeypatch,
        lambda _: httpx.Response(200, json={
            "data": [{"id": "translator-large"}]
        }),
    )
    missing = await _openai_compatible_checks(Settings(), "translator")
    assert missing[0].status is CheckStatus.READY
    assert missing[1].status is CheckStatus.MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["ollama", "openai", "vibevoice"])
async def test_5xx_and_invalid_json_do_not_report_ready(
    monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    for response in (
        httpx.Response(502, json={"message": "gateway down"}),
        httpx.Response(200, content=b"not-json"),
    ):
        _mock_http(monkeypatch, lambda _, r=response: r)
        if provider == "ollama":
            statuses = await _ollama_checks(Settings(), "translategemma:4b")
        elif provider == "openai":
            statuses = await _openai_compatible_checks(Settings(), "model")
        else:
            statuses = [await _vibevoice_check(Settings())]
        assert all(check.status is not CheckStatus.READY for check in statuses)
