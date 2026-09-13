from __future__ import annotations

from langtextflow.config import Settings
from langtextflow.mdns import MdnsPublisher, normalize_mdns_hostname


def test_normalize_mdns_hostname_is_single_label_and_local() -> None:
    assert normalize_mdns_hostname("My Caption Box.local") == "my-caption-box.local"


def test_mdns_publisher_is_failure_contained_without_lan_address(monkeypatch) -> None:
    settings = Settings(mdns_enabled=True, mdns_hostname="caption-room")
    monkeypatch.setattr("langtextflow.mdns.local_ipv4_addresses", lambda: [])

    publisher = MdnsPublisher(settings)
    state = publisher.start()

    assert state.hostname == "caption-room.local"
    assert state.ready is False
    assert state.error == "no eligible LAN IPv4 address is available for mDNS"


def test_mdns_publisher_registers_and_unregisters_service(monkeypatch) -> None:
    events: list[str] = []

    class FakeZeroconf:
        def __init__(self, *args, **kwargs) -> None:
            events.append("init")

        def register_service(self, info, allow_name_change: bool = False) -> None:
            assert allow_name_change is True
            assert info.port == 5173
            events.append("register")

        def unregister_service(self, info) -> None:
            events.append("unregister")

        def close(self) -> None:
            events.append("close")

    settings = Settings(mdns_enabled=True, mdns_hostname="caption-room", frontend_port=5173)
    monkeypatch.setattr("langtextflow.mdns.local_ipv4_addresses", lambda: ["192.168.1.25"])
    monkeypatch.setattr("langtextflow.mdns.Zeroconf", FakeZeroconf)

    publisher = MdnsPublisher(settings)
    state = publisher.start()
    assert state.hostname == "caption-room.local"
    assert state.ready is True
    assert state.error is None

    publisher.stop()
    assert publisher.state.ready is False
    assert events == ["init", "register", "unregister", "close"]
