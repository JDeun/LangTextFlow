"""Deterministic LAN discovery and browser-Origin security regressions (#82).

No real external socket, DNS service or user-owned operator session is touched.
"""
from __future__ import annotations

import socket

import pytest

from langtextflow import network


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1", "10.255.255.254",
        "172.16.0.1", "172.31.255.254",
        "192.168.0.10", "192.168.255.254",
        "100.64.0.1", "100.127.255.254",
    ],
)
def test_audience_ip_allowlist_accepts_only_supported_lan_vpn_ranges(
    address: str,
) -> None:
    assert network._allowed(address) is True


@pytest.mark.parametrize(
    "address",
    [
        "172.15.255.255", "172.32.0.1",
        "100.63.255.255", "100.128.0.1",
        "169.254.0.1",  # link-local is not routable across a LAN
        "0.0.0.0",  # bind-all interface is not a usable audience address
        "255.255.255.255",  # broadcast
        "198.18.0.1",  # RFC 2544 testing range, not a normal audience subnet
        "192.0.2.1",  # documentation-only
        "127.0.0.1", "8.8.8.8", "224.0.0.1",
        "::1", "fe80::1", "::ffff:192.168.1.1",
        "localhost", "192.168.1.1:8000", "",
    ],
)
def test_audience_ip_discovery_rejects_non_routable_or_non_supported_addresses(
    address: str,
) -> None:
    assert network._allowed(address) is False


class _FakeSocket:
    def __init__(self, source_ip: str, *, fail_connect: bool = False) -> None:
        self.source_ip = source_ip
        self.fail_connect = fail_connect
        self.destination: tuple[str, int] | None = None

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def connect(self, destination: tuple[str, int]) -> None:
        self.destination = destination
        if self.fail_connect:
            raise OSError("offline test: no route")

    def getsockname(self) -> tuple[str, int]:
        return self.source_ip, 52141


def test_local_ipv4_addresses_filters_dns_records_and_deduplicates_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_socket = _FakeSocket("10.1.2.3")
    monkeypatch.setattr(network.socket, "gethostname", lambda: "mock.local")
    monkeypatch.setattr(
        network.socket,
        "getaddrinfo",
        lambda _hostname, _port, _family: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.5", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.42.42", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("0.0.0.0", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.1.2.3", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.64.1.8", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0)),
        ],
    )
    monkeypatch.setattr(
        network.socket,
        "socket",
        lambda _family, _kind: fake_socket,
    )
    assert network.local_ipv4_addresses() == [
        "10.1.2.3", "100.64.1.8", "192.168.1.5"
    ]
    assert fake_socket.destination == ("8.8.8.8", 80)


def test_local_ipv4_addresses_returns_empty_during_dns_and_udp_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_dns(*_args: object) -> list[object]:
        raise OSError("DNS down")

    monkeypatch.setattr(network.socket, "gethostname", lambda: "mock.local")
    monkeypatch.setattr(network.socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(
        network.socket,
        "socket",
        lambda _family, _kind: _FakeSocket("0.0.0.0", fail_connect=True),
    )
    assert network.local_ipv4_addresses() == []


def test_operator_origin_never_inherits_audience_lan_policy() -> None:
    trusted = ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert network.operator_origin_allowed(None, allowed_origins=trusted)
    assert network.operator_origin_allowed(
        "http://localhost:5173", allowed_origins=trusted
    )
    for origin in (
        "", " ", "http://192.168.1.5:5173", "http://100.64.1.8:5173",
        "https://localhost:5173", "http://localhost:5173.attacker.example",
        "http://localhost:5173/", "http://localhost:5173@attacker.example",
    ):
        assert not network.operator_origin_allowed(origin, allowed_origins=trusted)


def test_operator_origin_rejects_nonloopback_even_if_configured() -> None:
    origin = "http://192.168.0.5:5173"
    assert network.operator_origin_allowed(origin, allowed_origins=[origin]) is False


def test_websocket_origin_accepts_native_none_but_rejects_cross_site_spoof() -> None:
    trusted = ["http://localhost:5173"]
    regex = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    assert network.websocket_origin_allowed(
        None, allowed_origins=trusted, allowed_origin_regex=regex
    )
    for origin in (
        "", "https://evil.example", "http://localhost.attacker.example:5173",
        "http://localhost:5173@evil.example", "http://localhost:5173/path",
        "http://127.0.0.1.evil.example:5173",
    ):
        assert not network.websocket_origin_allowed(
            origin, allowed_origins=trusted, allowed_origin_regex=regex
        )
