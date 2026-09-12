import pytest
from fastapi import HTTPException, Request

from langtextflow import main
from langtextflow.network import _allowed, is_loopback_client


def _request(host: str) -> Request:
    return Request({"type": "http", "client": (host, 12345)})


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
