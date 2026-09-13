from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from langtextflow import main as main_module
from langtextflow.audience_security import AudienceJoinRateLimiter
from langtextflow.models import AudioStreamInfo, SessionContext, SessionState


def _local_client() -> TestClient:
    return TestClient(
        main_module.app,
        client=("127.0.0.1", 50000),
    )


def test_operator_rest_does_not_trust_forwarded_for() -> None:
    client = TestClient(
        main_module.app,
        client=("203.0.113.20", 50000),
    )
    response = client.get(
        "/api/v1/state",
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 403


def test_operator_rest_rejects_hostile_origin_from_loopback() -> None:
    response = _local_client().post(
        "/api/v1/session/stop",
        headers={"Origin": "https://localhost.evil.example"},
    )
    assert response.status_code == 403


def test_operator_rest_rejects_cross_site_fetch_metadata() -> None:
    response = _local_client().get(
        "/api/v1/state",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_operator_rest_accepts_allowed_local_origin() -> None:
    response = _local_client().get(
        "/api/v1/state",
        headers={
            "Origin": "http://localhost:5173",
            "Sec-Fetch-Site": "same-site",
        },
    )
    assert response.status_code == 200


def test_operator_rest_accepts_native_client_without_browser_headers() -> None:
    response = _local_client().get("/api/v1/state")
    assert response.status_code == 200


def test_operator_websocket_rejects_hostile_origin_from_loopback() -> None:
    client = _local_client()
    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        "/ws/captions",
        headers={"origin": "https://localhost.evil.example"},
    ):
        pass
    assert exc_info.value.code == 4403


def test_audience_rate_limit_uses_socket_ip_not_forwarded_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = AudienceJoinRateLimiter(
        max_failures=2,
        window_seconds=60.0,
        block_seconds=60.0,
        max_clients=16,
    )
    monkeypatch.setattr(main_module, "audience_join_limiter", limiter)
    client = TestClient(
        main_module.app,
        client=("198.51.100.44", 50000),
    )

    first = client.get(
        "/api/v1/audience/INVALID",
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    second = client.get(
        "/api/v1/audience/INVALID",
        headers={"X-Forwarded-For": "10.0.0.2"},
    )

    assert first.status_code == 404
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert limiter.tracked_clients() == 1


def test_audience_websocket_origin_failure_does_not_consume_join_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = AudienceJoinRateLimiter(max_failures=1, max_clients=16)
    monkeypatch.setattr(main_module, "audience_join_limiter", limiter)
    client = TestClient(
        main_module.app,
        client=("198.51.100.45", 50000),
    )

    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        "/ws/audience/INVALID",
        headers={"origin": "https://evil.example"},
    ):
        pass

    assert exc_info.value.code == 4403
    assert limiter.tracked_clients() == 0


def test_audience_caption_socket_is_server_push_only(monkeypatch: pytest.MonkeyPatch) -> None:
    state = SessionState(
        session_id="session-boundary-test",
        join_code="ABC234",
        running=True,
        source_language="ko",
        target_languages=["en"],
        engine="mock",
        context=SessionContext(title="Boundary test"),
        started_at=datetime.now(UTC),
    )
    monkeypatch.setattr(main_module.runtime, "state", state)
    main_module.runtime.store.clear()
    client = TestClient(
        main_module.app,
        client=("192.168.1.40", 50000),
    )

    with client.websocket_connect(
        "/ws/audience/ABC234",
        headers={"origin": "http://192.168.1.40:5173"},
    ) as websocket:
        snapshot = websocket.receive_json()
        assert snapshot == {"type": "snapshot", "segments": []}
        websocket.send_text("clients must not publish captions")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
        assert exc_info.value.code == 4400


def test_remote_operator_request_is_rejected_before_json_body_parsing() -> None:
    client = TestClient(
        main_module.app,
        client=("203.0.113.55", 50000),
    )
    response = client.post(
        "/api/v1/session/start",
        content="{ definitely-not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "operator API is local-only"}


def test_audio_websocket_rejects_frame_above_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_feed(_: bytes) -> None:
        raise AssertionError("oversized frame reached runtime.feed_audio")

    monkeypatch.setattr(
        main_module.runtime,
        "audio_info",
        lambda: AudioStreamInfo(engine="test", required=True, sample_rate=16000),
    )
    monkeypatch.setattr(main_module.runtime, "feed_audio", unexpected_feed)

    client = _local_client()
    with client.websocket_connect(
        "/ws/audio",
        headers={"origin": "http://localhost:5173"},
    ) as websocket:
        config = websocket.receive_json()
        assert config["type"] == "audio_config"
        websocket.send_bytes(bytes(main_module.settings.max_audio_frame_bytes + 4))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()
        assert exc_info.value.code == 1009


def test_operator_rest_rejects_lan_origin_even_from_loopback_before_body_parse() -> None:
    response = _local_client().post(
        "/api/v1/session/start",
        content="{ definitely-not-json",
        headers={
            "Content-Type": "application/json",
            "Origin": "http://192.168.1.40:5173",
        },
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "operator browser origin is not allowed"}


def test_operator_websocket_rejects_lan_origin_from_loopback() -> None:
    client = _local_client()
    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
        "/ws/captions",
        headers={"origin": "http://192.168.1.40:5173"},
    ):
        pass
    assert exc_info.value.code == 4403


def test_only_one_audio_websocket_can_own_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module.runtime,
        "audio_info",
        lambda: AudioStreamInfo(engine="test", required=True, sample_rate=16000),
    )
    client = _local_client()
    with client.websocket_connect(
        "/ws/audio",
        headers={"origin": "http://localhost:5173"},
    ) as first:
        assert first.receive_json()["type"] == "audio_config"
        with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect(
            "/ws/audio",
            headers={"origin": "http://localhost:5173"},
        ):
            pass
        assert exc_info.value.code == 4409
        first.send_text("end")
