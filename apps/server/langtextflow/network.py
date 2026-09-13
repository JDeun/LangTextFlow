from __future__ import annotations

import re
import socket
from ipaddress import IPv4Address, ip_address, ip_network

_SHARED = ip_network("100.64.0.0/10")


def _allowed(address: str) -> bool:
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return isinstance(parsed, IPv4Address) and (
        parsed.is_private or parsed in _SHARED
    ) and not parsed.is_loopback


def local_ipv4_addresses() -> list[str]:
    """Return useful LAN/VPN IPv4 candidates without requiring external packages."""
    candidates: set[str] = set()

    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(result[4][0])
            if _allowed(address):
                candidates.add(address)
    except OSError:
        pass

    # UDP connect selects the OS' primary route without sending application data.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = str(probe.getsockname()[0])
            if _allowed(address):
                candidates.add(address)
    except OSError:
        pass

    return sorted(candidates)


def is_loopback_client(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def operator_origin_allowed(origin: str | None, *, allowed_origins: list[str]) -> bool:
    """Allow only explicitly configured operator browser origins.

    Operator traffic always terminates on loopback, so LAN/private/mDNS origins that
    are intentionally valid for audience pages must not inherit operator privileges.
    Native local clients may omit Origin and are authorized by the socket boundary.
    """

    if origin is None:
        return True
    normalized = origin.strip()
    return bool(normalized) and normalized in allowed_origins


def websocket_origin_allowed(
    origin: str | None,
    *,
    allowed_origins: list[str],
    allowed_origin_regex: str,
) -> bool:
    """Validate the broader audience WebSocket Origin policy.

    Audience pages can legitimately be served from LAN/private/Tailscale/mDNS hosts.
    Operator WebSockets must use :func:`operator_origin_allowed` instead.
    """

    if origin is None:
        return True
    normalized = origin.strip()
    if not normalized:
        return False
    if normalized in allowed_origins:
        return True
    return re.fullmatch(allowed_origin_regex, normalized) is not None
