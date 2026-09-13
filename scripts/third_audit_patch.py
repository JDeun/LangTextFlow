from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}: {old[:80]!r}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def append_once(path: str, marker: str, content: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n\n" + content.strip() + "\n", encoding="utf-8")


# Strictly separate operator browser origin trust from audience LAN/mDNS CORS policy.
replace(
    "apps/server/langtextflow/main.py",
    "from typing import Annotated\n",
    "from threading import Lock\nfrom typing import Annotated\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "from .network import is_loopback_client, local_ipv4_addresses, websocket_origin_allowed\n",
    "from .network import (\n"
    "    is_loopback_client,\n"
    "    local_ipv4_addresses,\n"
    "    operator_origin_allowed,\n"
    "    websocket_origin_allowed,\n"
    ")\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "audience_join_limiter = AudienceJoinRateLimiter(\n"
    "    max_failures=settings.audience_join_max_failures,\n"
    "    window_seconds=settings.audience_join_window_seconds,\n"
    "    block_seconds=settings.audience_join_block_seconds,\n"
    "    max_clients=settings.audience_join_max_tracked_clients,\n"
    ")\n",
    "audience_join_limiter = AudienceJoinRateLimiter(\n"
    "    max_failures=settings.audience_join_max_failures,\n"
    "    window_seconds=settings.audience_join_window_seconds,\n"
    "    block_seconds=settings.audience_join_block_seconds,\n"
    "    max_clients=settings.audience_join_max_tracked_clients,\n"
    ")\n"
    "_audio_socket_guard = Lock()\n"
    "_active_audio_socket: WebSocket | None = None\n"
    "_audio_socket_accepting = True\n\n\n"
    "def _claim_audio_socket(websocket: WebSocket) -> bool:\n"
    "    global _active_audio_socket\n"
    "    with _audio_socket_guard:\n"
    "        if not _audio_socket_accepting or _active_audio_socket is not None:\n"
    "            return False\n"
    "        _active_audio_socket = websocket\n"
    "        return True\n\n\n"
    "def _release_audio_socket(websocket: WebSocket) -> None:\n"
    "    global _active_audio_socket\n"
    "    with _audio_socket_guard:\n"
    "        if _active_audio_socket is websocket:\n"
    "            _active_audio_socket = None\n\n\n"
    "def _block_audio_socket_acceptance() -> WebSocket | None:\n"
    "    global _active_audio_socket, _audio_socket_accepting\n"
    "    with _audio_socket_guard:\n"
    "        _audio_socket_accepting = False\n"
    "        websocket = _active_audio_socket\n"
    "        _active_audio_socket = None\n"
    "        return websocket\n\n\n"
    "def _allow_audio_socket_acceptance() -> None:\n"
    "    global _audio_socket_accepting\n"
    "    with _audio_socket_guard:\n"
    "        _audio_socket_accepting = True\n\n\n"
    "async def _close_audio_socket_for_transition(reason: str) -> None:\n"
    "    websocket = _block_audio_socket_acceptance()\n"
    "    if websocket is None:\n"
    "        return\n"
    "    try:\n"
    "        await asyncio.wait_for(websocket.close(code=1012, reason=reason), timeout=1.0)\n"
    "    except Exception:\n"
    "        pass\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "    finally:\n"
    "        await runtime.shutdown()\n"
    "        await vibevoice_lifecycle.shutdown()\n",
    "    finally:\n"
    "        await _close_audio_socket_for_transition(\"server shutting down\")\n"
    "        await runtime.shutdown()\n"
    "        await vibevoice_lifecycle.shutdown()\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "        if not is_loopback_client(host):\n"
    "            return Response(\n"
    "                content='{\"detail\":\"operator API is local-only\"}',\n"
    "                status_code=403,\n"
    "                media_type=\"application/json\",\n"
    "            )\n",
    "        if not is_loopback_client(host):\n"
    "            return Response(\n"
    "                content='{\"detail\":\"operator API is local-only\"}',\n"
    "                status_code=403,\n"
    "                media_type=\"application/json\",\n"
    "            )\n"
    "        if not _operator_request_origin_allowed(request):\n"
    "            return Response(\n"
    "                content='{\"detail\":\"operator browser origin is not allowed\"}',\n"
    "                status_code=403,\n"
    "                media_type=\"application/json\",\n"
    "            )\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "    return websocket_origin_allowed(\n"
    "        request.headers.get(\"origin\"),\n"
    "        allowed_origins=settings.cors_origins,\n"
    "        allowed_origin_regex=settings.cors_origin_regex,\n"
    "    )\n",
    "    return operator_origin_allowed(\n"
    "        request.headers.get(\"origin\"),\n"
    "        allowed_origins=settings.cors_origins,\n"
    "    )\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "def _operator_websocket_allowed(websocket: WebSocket) -> bool:\n"
    "    host = websocket.client.host if websocket.client else None\n"
    "    return is_loopback_client(host) and _websocket_origin_allowed(websocket)\n",
    "def _operator_websocket_allowed(websocket: WebSocket) -> bool:\n"
    "    host = websocket.client.host if websocket.client else None\n"
    "    return is_loopback_client(host) and operator_origin_allowed(\n"
    "        websocket.headers.get(\"origin\"),\n"
    "        allowed_origins=settings.cors_origins,\n"
    "    )\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "async def start_session(request: Request, payload: StartSessionRequest) -> SessionState:\n"
    "    _require_operator(request)\n"
    "    try:\n"
    "        return await runtime.start(_with_saved_glossary(payload))\n"
    "    except AsrEngineError as exc:\n"
    "        raise HTTPException(status_code=503, detail=str(exc)) from exc\n"
    "    except ValueError as exc:\n"
    "        raise HTTPException(status_code=400, detail=str(exc)) from exc\n\n\n"
    "@app.post(\"/api/v1/session/stop\", response_model=SessionState)\n"
    "async def stop_session(request: Request) -> SessionState:\n"
    "    _require_operator(request)\n"
    "    return await runtime.stop()\n",
    "async def start_session(request: Request, payload: StartSessionRequest) -> SessionState:\n"
    "    _require_operator(request)\n"
    "    await _close_audio_socket_for_transition(\"caption session restarting\")\n"
    "    try:\n"
    "        return await runtime.start(_with_saved_glossary(payload))\n"
    "    except AsrEngineError as exc:\n"
    "        raise HTTPException(status_code=503, detail=str(exc)) from exc\n"
    "    except ValueError as exc:\n"
    "        raise HTTPException(status_code=400, detail=str(exc)) from exc\n"
    "    finally:\n"
    "        _allow_audio_socket_acceptance()\n\n\n"
    "@app.post(\"/api/v1/session/stop\", response_model=SessionState)\n"
    "async def stop_session(request: Request) -> SessionState:\n"
    "    _require_operator(request)\n"
    "    await _close_audio_socket_for_transition(\"caption session stopping\")\n"
    "    try:\n"
    "        return await runtime.stop()\n"
    "    finally:\n"
    "        _allow_audio_socket_acceptance()\n",
)
replace(
    "apps/server/langtextflow/main.py",
    "    await websocket.accept()\n"
    "    await websocket.send_json({\"type\": \"audio_config\", **info.model_dump(mode=\"json\")})\n"
    "    try:\n"
    "        while True:\n"
    "            message = await websocket.receive()\n"
    "            if message[\"type\"] == \"websocket.disconnect\":\n"
    "                break\n"
    "            frame = message.get(\"bytes\")\n"
    "            text = message.get(\"text\")\n"
    "            if frame is not None:\n"
    "                if len(frame) > settings.max_audio_frame_bytes:\n"
    "                    await websocket.close(\n"
    "                        code=1009,\n"
    "                        reason=\"audio frame exceeds configured safety limit\",\n"
    "                    )\n"
    "                    break\n"
    "                await runtime.feed_audio(frame)\n"
    "            elif text == \"end\":\n"
    "                await runtime.end_audio()\n"
    "                break\n"
    "            else:\n"
    "                await websocket.close(code=4400, reason=\"invalid audio control message\")\n"
    "                break\n"
    "    except WebSocketDisconnect:\n"
    "        pass\n"
    "    except (AsrEngineError, ValueError) as exc:\n"
    "        await websocket.close(code=1011, reason=str(exc)[:120])\n",
    "    if not _claim_audio_socket(websocket):\n"
    "        await websocket.close(code=4409, reason=\"another audio source is already connected\")\n"
    "        return\n\n"
    "    try:\n"
    "        await websocket.accept()\n"
    "        await websocket.send_json({\"type\": \"audio_config\", **info.model_dump(mode=\"json\")})\n"
    "        try:\n"
    "            while True:\n"
    "                message = await websocket.receive()\n"
    "                if message[\"type\"] == \"websocket.disconnect\":\n"
    "                    break\n"
    "                frame = message.get(\"bytes\")\n"
    "                text = message.get(\"text\")\n"
    "                if frame is not None:\n"
    "                    if len(frame) > settings.max_audio_frame_bytes:\n"
    "                        await websocket.close(\n"
    "                            code=1009,\n"
    "                            reason=\"audio frame exceeds configured safety limit\",\n"
    "                        )\n"
    "                        break\n"
    "                    await runtime.feed_audio(frame)\n"
    "                elif text == \"end\":\n"
    "                    await runtime.end_audio()\n"
    "                    break\n"
    "                else:\n"
    "                    await websocket.close(code=4400, reason=\"invalid audio control message\")\n"
    "                    break\n"
    "        except WebSocketDisconnect:\n"
    "            pass\n"
    "        except (AsrEngineError, RuntimeError, ValueError) as exc:\n"
    "            await websocket.close(code=1011, reason=str(exc)[:120])\n"
    "    finally:\n"
    "        _release_audio_socket(websocket)\n",
)

# Ensure auto fallback's replay buffer and active provider see frames in one serialized order.
replace(
    "apps/server/langtextflow/asr/fallback.py",
    "        self._remember_audio(pcm_f32le)\n"
    "        async with self._lock:\n"
    "            active = self._active\n"
    "            if active is None:\n"
    "                raise AsrEngineError(\"no ASR provider is active\")\n",
    "        async with self._lock:\n"
    "            active = self._active\n"
    "            if active is None:\n"
    "                raise AsrEngineError(\"no ASR provider is active\")\n"
    "            self._remember_audio(pcm_f32le)\n",
)

# Malformed-but-valid JSON from Ollama must degrade through provider-specific errors, not 500s.
for path, error_name, label in [
    ("apps/server/langtextflow/translation/ollama.py", "TranslationError", "Ollama model list response"),
    ("apps/server/langtextflow/llm_correction.py", "CorrectionError", "Ollama model list response"),
]:
    replace(
        path,
        "        names = {\n"
        "            str(item.get(\"name\") or item.get(\"model\") or \"\")\n"
        "            for item in payload.get(\"models\", [])\n"
        "            if isinstance(item, dict)\n"
        "        }\n",
        "        if not isinstance(payload, dict):\n"
        f"            raise {error_name}(\"{label} must be a JSON object\")\n"
        "        models = payload.get(\"models\", [])\n"
        "        if not isinstance(models, list):\n"
        f"            raise {error_name}(\"{label} models field must be a list\")\n"
        "        names = {\n"
        "            str(item.get(\"name\") or item.get(\"model\") or \"\")\n"
        "            for item in models\n"
        "            if isinstance(item, dict)\n"
        "        }\n",
    )

# Engine startup should not become audience-visible before the provider is actually ready.
replace(
    "apps/server/langtextflow/runtime.py",
    "            running=True,\n"
    "            source_language=request.source_language,\n",
    "            running=False,\n"
    "            source_language=request.source_language,\n",
)
replace(
    "apps/server/langtextflow/runtime.py",
    "            self.state.audio_sample_rate = engine.sample_rate\n"
    "            self._refresh_queue_metrics()\n",
    "            self.state.audio_sample_rate = engine.sample_rate\n"
    "            self.state.running = True\n"
    "            self._refresh_queue_metrics()\n",
)
replace(
    "apps/server/langtextflow/runtime.py",
    "            if self.state.session_id and self.state.persistence_error is None:\n"
    "                try:\n"
    "                    await asyncio.to_thread(self.history.mark_ended, self.state.session_id)\n"
    "                except Exception as exc:\n"
    "                    self.state.persistence_error = str(exc)\n"
    "            raise\n",
    "            if self.state.session_id and self.state.persistence_error is None:\n"
    "                try:\n"
    "                    await asyncio.to_thread(self.history.mark_ended, self.state.session_id)\n"
    "                except Exception as exc:\n"
    "                    self.state.persistence_error = str(exc)\n"
    "            await self.hub.close_all(reason=\"caption session failed to start\")\n"
    "            raise\n",
)

# Browser preflight HTTP errors must block session startup instead of silently bypassing checks.
replace(
    "apps/web/src/App.tsx",
    "    const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);\n"
    "    if (!response.ok) return;\n"
    "    const report = (await response.json()) as SystemPreflight;\n",
    "    const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);\n"
    "    if (!response.ok) {\n"
    "      const detail = await response.text();\n"
    "      throw new Error(detail || `사전점검 요청에 실패했습니다. (${response.status})`);\n"
    "    }\n"
    "    const report = (await response.json()) as SystemPreflight;\n",
)
replace(
    "apps/web/src/App.tsx",
    "  const { connected, segments } = useCaptionSocket(`/ws/audience/${encodeURIComponent(joinCode)}`);\n",
    "  const { connected, segments, terminalError } = useCaptionSocket(\n"
    "    `/ws/audience/${encodeURIComponent(joinCode)}`,\n"
    "  );\n",
)
replace(
    "apps/web/src/App.tsx",
    "  if (error) return <main className=\"audience-error\">{error}</main>;\n",
    "  if (error || terminalError) {\n"
    "    return <main className=\"audience-error\">{error || terminalError}</main>;\n"
    "  }\n",
)
replace(
    "apps/web/src/App.tsx",
    "  const { connected, segments } = useCaptionSocket();\n",
    "  const { connected, segments, terminalError } = useCaptionSocket();\n",
)
replace(
    "apps/web/src/App.tsx",
    "          {error && <div className=\"error-box\">{error}</div>}\n",
    "          {(error || terminalError) && (\n"
    "            <div className=\"error-box\">{error || terminalError}</div>\n"
    "          )}\n",
)

# Regression tests for newly identified third-angle failures.
append_once(
    "apps/server/tests/test_config.py",
    "test_settings_reject_zero_asr_replay_window",
    """
def test_settings_reject_zero_asr_replay_window() -> None:
    with pytest.raises(ValidationError):
        Settings(asr_replay_seconds=0)
""",
)
append_once(
    "apps/server/tests/test_runtime_lifecycle.py",
    "test_invalid_engine_does_not_leave_pipeline_worker_running",
    """
@pytest.mark.asyncio
async def test_invalid_engine_does_not_leave_pipeline_worker_running(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    request = StartSessionRequest(
        engine="definitely-invalid",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
    )

    with pytest.raises(ValueError, match="unsupported engine"):
        await runtime.start(request)

    assert runtime.state.running is False
    assert runtime.engine is None
    assert runtime.pipeline._worker_task is None
    assert runtime._persistence_task is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stopped_session_join_code_is_immediately_invalid(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    state = await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    join_code = state.join_code
    assert join_code is not None
    assert runtime.audience_view(join_code).running is True

    await runtime.stop()
    with pytest.raises(KeyError):
        runtime.audience_view(join_code)
    await runtime.shutdown()


class _FakeCaptionSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason or "")

    async def send_json(self, payload: object) -> None:
        del payload


@pytest.mark.asyncio
async def test_new_session_disconnects_existing_caption_subscribers(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    request = StartSessionRequest(
        engine="mock",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
    )
    await runtime.start(request)
    socket = _FakeCaptionSocket()
    assert await runtime.hub.connect(socket) is True
    assert runtime.hub.client_count == 1

    await runtime.start(request)
    assert socket.closed == (1012, "caption session stopped")
    assert runtime.hub.client_count == 0
    await runtime.shutdown()
""",
)
append_once(
    "apps/server/tests/test_api_boundaries.py",
    "test_operator_rest_rejects_lan_origin_even_from_loopback_before_body_parse",
    """
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
""",
)
append_once(
    "apps/server/tests/test_translation.py",
    "test_ollama_prepare_rejects_non_object_payload",
    """

class _BadPrepareResponse:
    def raise_for_status(self) -> None:
        return

    def json(self) -> list[object]:
        return []


class _BadPrepareClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "_BadPrepareClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def get(self, url: str) -> _BadPrepareResponse:
        assert url.endswith("/api/tags")
        return _BadPrepareResponse()


@pytest.mark.asyncio
async def test_ollama_prepare_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("langtextflow.translation.ollama.httpx.AsyncClient", _BadPrepareClient)
    translator = OllamaTranslator(base_url="http://127.0.0.1:11434", model="test-model")

    with pytest.raises(TranslationError, match="JSON object"):
        await translator.prepare()
""",
)
replace(
    "apps/server/tests/test_translation.py",
    "from langtextflow.models import GlossaryEntry, SessionContext\n"
    "from langtextflow.translation.ollama import OllamaTranslator\n",
    "import pytest\n\n"
    "from langtextflow.models import GlossaryEntry, SessionContext\n"
    "from langtextflow.translation.base import TranslationError\n"
    "from langtextflow.translation.ollama import OllamaTranslator\n",
)
append_once(
    "apps/server/tests/test_llm_correction.py",
    "test_ollama_corrector_prepare_rejects_non_object_payload",
    """

class _BadPrepareResponse:
    def raise_for_status(self) -> None:
        return

    def json(self) -> list[object]:
        return []


class _BadPrepareClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "_BadPrepareClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def get(self, url: str) -> _BadPrepareResponse:
        assert url.endswith("/api/tags")
        return _BadPrepareResponse()


@pytest.mark.asyncio
async def test_ollama_corrector_prepare_rejects_non_object_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("langtextflow.llm_correction.httpx.AsyncClient", _BadPrepareClient)
    corrector = OllamaConstrainedCorrector(
        base_url="http://127.0.0.1:11434",
        model="test-model",
    )

    with pytest.raises(CorrectionError, match="JSON object"):
        await corrector.prepare()
""",
)

# Remove this one-shot driver from the resulting product commit.
Path(__file__).unlink()
