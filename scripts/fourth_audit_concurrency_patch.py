from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found: {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "apps/server/langtextflow/runtime.py",
    '''    async def feed_audio(self, pcm_f32le: bytes) -> None:\n        if self.engine is None or not self.state.running or not self.engine.accepts_audio:\n            raise RuntimeError("there is no active audio ASR session")\n\n        dbfs, voice_active = self._vad.analyze(pcm_f32le)\n        self.metrics.audio_frames_received += 1\n        self.metrics.audio_bytes_received += len(pcm_f32le)\n        self.metrics.audio_duration_ms += round(\n            (len(pcm_f32le) / 4 / self.engine.sample_rate) * 1000.0,\n            3,\n        )\n        self.metrics.audio_rms_dbfs = dbfs\n        self.metrics.voice_active = voice_active\n\n        started = time.perf_counter()\n        try:\n            await self.engine.feed_audio(pcm_f32le)\n        finally:\n            self._refresh_queue_metrics()\n''',
    '''    async def feed_audio(self, pcm_f32le: bytes) -> None:\n        # Capture the active provider once. A concurrent stop intentionally clears\n        # self.engine before awaiting provider shutdown; repeatedly dereferencing\n        # self.engine here could otherwise turn that race into AttributeError.\n        engine = self.engine\n        if engine is None or not self.state.running or not engine.accepts_audio:\n            raise RuntimeError("there is no active audio ASR session")\n\n        dbfs, voice_active = self._vad.analyze(pcm_f32le)\n        self.metrics.audio_frames_received += 1\n        self.metrics.audio_bytes_received += len(pcm_f32le)\n        self.metrics.audio_duration_ms += round(\n            (len(pcm_f32le) / 4 / engine.sample_rate) * 1000.0,\n            3,\n        )\n        self.metrics.audio_rms_dbfs = dbfs\n        self.metrics.voice_active = voice_active\n\n        started = time.perf_counter()\n        try:\n            await engine.feed_audio(pcm_f32le)\n        finally:\n            self._refresh_queue_metrics()\n''',
)

replace(
    "apps/server/langtextflow/runtime.py",
    '''    async def end_audio(self) -> None:\n        if self.engine is not None and self.engine.accepts_audio:\n            await self.engine.end_audio()\n            self._refresh_queue_metrics()\n\n    def audio_info(self) -> AudioStreamInfo:\n        if self.engine is None or not self.state.running:\n            raise RuntimeError("there is no active session")\n        return AudioStreamInfo(\n            engine=self.state.engine,\n            required=self.engine.accepts_audio,\n            sample_rate=self.engine.sample_rate,\n        )\n''',
    '''    async def end_audio(self) -> None:\n        engine = self.engine\n        if engine is not None and engine.accepts_audio:\n            await engine.end_audio()\n            self._refresh_queue_metrics()\n\n    def audio_info(self) -> AudioStreamInfo:\n        engine = self.engine\n        if engine is None or not self.state.running:\n            raise RuntimeError("there is no active session")\n        return AudioStreamInfo(\n            engine=self.state.engine,\n            required=engine.accepts_audio,\n            sample_rate=engine.sample_rate,\n        )\n''',
)

replace(
    "apps/server/langtextflow/main.py",
    '''_audio_socket_guard = Lock()\n_active_audio_socket: WebSocket | None = None\n_audio_socket_accepting = True\n''',
    '''_audio_socket_guard = Lock()\n_audio_transition_lock = asyncio.Lock()\n_active_audio_socket: WebSocket | None = None\n_audio_socket_accepting = True\n''',
)

replace(
    "apps/server/langtextflow/main.py",
    '''async def start_session(request: Request, payload: StartSessionRequest) -> SessionState:\n    _require_operator(request)\n    await _close_audio_socket_for_transition("caption session restarting")\n    try:\n        return await runtime.start(_with_saved_glossary(payload))\n    except AsrEngineError as exc:\n        raise HTTPException(status_code=503, detail=str(exc)) from exc\n    except ValueError as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n    finally:\n        _allow_audio_socket_acceptance()\n''',
    '''async def start_session(request: Request, payload: StartSessionRequest) -> SessionState:\n    _require_operator(request)\n    async with _audio_transition_lock:\n        await _close_audio_socket_for_transition("caption session restarting")\n        try:\n            return await runtime.start(_with_saved_glossary(payload))\n        except AsrEngineError as exc:\n            raise HTTPException(status_code=503, detail=str(exc)) from exc\n        except ValueError as exc:\n            raise HTTPException(status_code=400, detail=str(exc)) from exc\n        finally:\n            _allow_audio_socket_acceptance()\n''',
)

replace(
    "apps/server/langtextflow/main.py",
    '''async def stop_session(request: Request) -> SessionState:\n    _require_operator(request)\n    await _close_audio_socket_for_transition("caption session stopping")\n    try:\n        return await runtime.stop()\n    finally:\n        _allow_audio_socket_acceptance()\n''',
    '''async def stop_session(request: Request) -> SessionState:\n    _require_operator(request)\n    async with _audio_transition_lock:\n        await _close_audio_socket_for_transition("caption session stopping")\n        try:\n            return await runtime.stop()\n        finally:\n            _allow_audio_socket_acceptance()\n''',
)

runtime_test = Path("apps/server/tests/test_runtime_lifecycle.py")
text = runtime_test.read_text(encoding="utf-8")
addition = r'''

class _RaceAudioEngine:
    accepts_audio = True
    sample_rate = 16000
    queue_depth = 0
    queue_capacity = 1
    failure = None
    running = True

    def __init__(self) -> None:
        self.received = False

    async def feed_audio(self, frame: bytes) -> None:
        assert frame
        self.received = True

    async def end_audio(self) -> None:
        return


@pytest.mark.asyncio
async def test_feed_audio_uses_captured_engine_during_concurrent_stop_boundary(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "runtime.db")))
    engine = _RaceAudioEngine()
    runtime.engine = engine  # type: ignore[assignment]
    runtime.state.running = True

    original_analyze = runtime._vad.analyze

    def detach_engine_after_validation(frame: bytes) -> tuple[float, bool]:
        result = original_analyze(frame)
        runtime.engine = None
        return result

    runtime._vad.analyze = detach_engine_after_validation  # type: ignore[method-assign]
    frame = b"\x00\x00\x00\x00" * 32
    await runtime.feed_audio(frame)

    assert engine.received is True
'''
if "test_feed_audio_uses_captured_engine_during_concurrent_stop_boundary" not in text:
    runtime_test.write_text(text + addition, encoding="utf-8")

Path("scripts/fourth_audit_concurrency_patch.py").unlink()
