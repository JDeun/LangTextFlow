from __future__ import annotations

import hashlib
import re
import socket
import uuid
from dataclasses import dataclass

from zeroconf import IPVersion, ServiceInfo, Zeroconf

from .config import Settings
from .network import local_ipv4_addresses

_HOST_LABEL_RE = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class MdnsState:
    hostname: str | None
    ready: bool
    error: str | None = None


def normalize_mdns_hostname(configured: str = "") -> str:
    """Return a conservative single-label .local hostname.

    The default intentionally avoids exposing the machine's user-visible host name.
    A short stable suffix reduces collisions when multiple LangTextFlow instances are
    used on the same LAN. Operators can override it with LANGTEXTFLOW_MDNS_HOSTNAME.
    """

    raw = configured.strip().lower()
    if raw.endswith(".local"):
        raw = raw[:-6]
    if not raw:
        suffix = hashlib.sha256(str(uuid.getnode()).encode("ascii")).hexdigest()[:6]
        raw = f"langtextflow-{suffix}"
    label = _HOST_LABEL_RE.sub("-", raw).strip("-")[:63].rstrip("-")
    if not label:
        raise ValueError("mDNS hostname must contain at least one letter or number")
    return f"{label}.local"


class MdnsPublisher:
    """Best-effort local HTTP service advertisement for audience URLs.

    mDNS failure must never prevent the caption server from starting. The explicit
    LAN IP list remains the fallback and is always returned by /api/v1/network.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._zeroconf: Zeroconf | None = None
        self._service: ServiceInfo | None = None
        self._state = MdnsState(hostname=None, ready=False)

    @property
    def state(self) -> MdnsState:
        return self._state

    def start(self) -> MdnsState:
        if self._zeroconf is not None:
            return self._state
        if not self.settings.mdns_enabled:
            self._state = MdnsState(hostname=None, ready=False)
            return self._state

        try:
            hostname = normalize_mdns_hostname(self.settings.mdns_hostname)
            addresses = local_ipv4_addresses()
            if not addresses:
                self._state = MdnsState(
                    hostname=hostname,
                    ready=False,
                    error="no eligible LAN IPv4 address is available for mDNS",
                )
                return self._state

            server = f"{hostname}."
            service_name = (self.settings.mdns_service_name.strip() or "LangTextFlow")[:80]
            instance = f"{service_name}._http._tcp.local."
            info = ServiceInfo(
                "_http._tcp.local.",
                instance,
                addresses=[socket.inet_aton(address) for address in addresses],
                port=self.settings.frontend_port,
                properties={
                    b"path": b"/",
                    b"product": b"LangTextFlow",
                },
                server=server,
            )
            zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
            zeroconf.register_service(info, allow_name_change=True)
            self._zeroconf = zeroconf
            self._service = info
            self._state = MdnsState(hostname=hostname, ready=True)
        except Exception as exc:  # pragma: no cover - OS/network specific
            self._state = MdnsState(
                hostname=self._safe_hostname(),
                ready=False,
                error=str(exc)[:500],
            )
        return self._state

    def stop(self) -> None:
        zeroconf = self._zeroconf
        service = self._service
        self._zeroconf = None
        self._service = None
        if zeroconf is None:
            return

        error: str | None = None
        if service is not None:
            try:
                zeroconf.unregister_service(service)
            except Exception as exc:  # pragma: no cover - OS/network specific
                error = str(exc)[:500]
        try:
            zeroconf.close()
        except Exception as exc:  # pragma: no cover - OS/network specific
            error = error or str(exc)[:500]
        self._state = MdnsState(
            hostname=self._safe_hostname(),
            ready=False,
            error=error,
        )

    def _safe_hostname(self) -> str | None:
        if not self.settings.mdns_enabled:
            return None
        try:
            return normalize_mdns_hostname(self.settings.mdns_hostname)
        except ValueError:
            return None
