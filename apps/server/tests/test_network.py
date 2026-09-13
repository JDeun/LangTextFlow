import pytest
from fastapi import HTTPException, Request

from langtextflow import main
from langtextflow.network import (
    _allowed,
    is_loopback_client,
    operator_origin_allowed,
    websocket_origin_allowed,
)


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "client": (host, 12345),
            "headers": [],
        }
    )


def test_loopback_client_detection() -> None:
    assert is_loopback_client("127.0.0.1") is True
    assert is_loopback_client("::1") is True
    assert is_loopback_client("localhost") is True
    assert is_loopback_client("192.168.1.20") is False


def test_audience_address_filter_accepts_private_and_cgnat() -> None:
    assert _allowed("192.168.1.20") is True
    assert _allowed("10.0.0.8") is True
    assert _allowed("100.64.12.4") is True
    assert _allowed("127.0.0.1") is False
    assert _allowed("8.8.8.8") is False


def test_operator_api_rejects_non_loopback_clients() -> None:
    main._require_operator(_request("127.0.0.1"))
    with pytest.raises(HTTPException) as error:
        main._require_operator(_request("192.168.1.20"))
    assert error.value.status_code == 403


def test_operator_origin_policy_is_exact_and_does_not_inherit_audience_hosts() -> None:
    allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

    assert operator_origin_allowed(None, allowed_origins=allowed_origins)
    assert operator_origin_allowed(
        "http://localhost:5173",
        allowed_origins=allowed_origins,
    )
    assert not operator_origin_allowed(
        "http://192.168.1.50:5173",
        allowed_origins=allowed_origins,
    )
    assert not operator_origin_allowed(
        "http://malicious.local:5173",
        allowed_origins=allowed_origins,
    )
    assert not operator_origin_allowed("", allowed_origins=allowed_origins)


def test_websocket_origin_policy_accepts_local_private_and_native_clients() -> None:
    allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    origin_regex = (
        r"^https?://(localhost|127\.0\.0\.1|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})"
        r"(:\d+)?$"
    )

    assert websocket_origin_allowed(
        None,
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )
    assert websocket_origin_allowed(
        "http://localhost:5173",
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )
    assert websocket_origin_allowed(
        "http://192.168.1.50:5173",
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )
    assert websocket_origin_allowed(
        "http://100.64.12.4:5173",
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )


def test_websocket_origin_policy_rejects_cross_site_and_blank_origins() -> None:
    allowed_origins = ["http://localhost:5173"]
    origin_regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    assert not websocket_origin_allowed(
        "https://evil.example",
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )
    assert not websocket_origin_allowed(
        "",
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )
    assert not websocket_origin_allowed(
        "http://localhost.attacker.example:5173",
        allowed_origins=allowed_origins,
        allowed_origin_regex=origin_regex,
    )
