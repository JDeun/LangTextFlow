from __future__ import annotations

import re
import socket
from ipaddress import IPv4Address, ip_address, ip_network
from urllib.parse import urlparse

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
    """Allow browser operator traffic only from explicitly configured loopback origins.

    Audience pages may legitimately run on LAN, Tailscale, or mDNS origins. Operator
    controls must not inherit that broad policy: a hostile LAN page could otherwise
    drive localhost APIs through the user's browser. Native/CLI clients may omit Origin.
    """
    if origin is None:
        return True
    normalized = origin.strip()
    if not normalized or normalized not in allowed_origins:
        return False
    try:
        parsed = urlparse(normalized)
        host = parsed.hostname
    except ValueError:
        return False
    return is_loopback_client(host)


def websocket_origin_allowed(
    origin: str | None,
    *,
    allowed_origins: list[str],
    allowed_origin_regex: str,
) -> bool:
    """Reject cross-site browser WebSockets while preserving non-browser clients.

    Browsers send an Origin header for WebSocket handshakes. CLI/native clients may
    omit it, so a missing Origin is accepted and authorization remains the endpoint's
    responsibility. When Origin is present it must match the same local/LAN/Tailscale
    policy used by the HTTP CORS configuration.
    """

    if origin is None:
        return True
    normalized = origin.strip()
    if not normalized:
        return False
    if normalized in allowed_origins:
        return True
    return re.fullmatch(allowed_origin_regex, normalized) is not None
