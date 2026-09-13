from pathlib import Path

root = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: patch anchor changed")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "apps/server/langtextflow/runtime.py",
    "        self._vad = EnergyVad(\n"
    "            threshold_dbfs=self.settings.vad_threshold_dbfs,\n"
    "            hangover_frames=self.settings.vad_hangover_frames,\n"
    "        )\n",
    "        self._vad = EnergyVad(\n"
    "            threshold_dbfs=self.settings.vad_threshold_dbfs,\n"
    "            hangover_frames=self.settings.vad_hangover_frames,\n"
    "            max_frame_bytes=self.settings.max_audio_frame_bytes,\n"
    "        )\n",
)

replace(
    "apps/web/src/App.tsx",
    "        const capture = new AudioCaptureController();\n"
    "        captureRef.current = capture;\n"
    "        await capture.start(selectedDevice);\n",
    "        const capture = new AudioCaptureController((message) => {\n"
    "          setError(message);\n"
    "          void (async () => {\n"
    "            await capture.stop().catch(() => undefined);\n"
    "            if (captureRef.current === capture) captureRef.current = null;\n"
    "            const stopResponse = await fetch(`${API_URL}/api/v1/session/stop`, {\n"
    "              method: \"POST\",\n"
    "            }).catch(() => null);\n"
    "            if (stopResponse?.ok) {\n"
    "              setSession((await stopResponse.json()) as SessionState);\n"
    "              setHistoryRefreshToken((value) => value + 1);\n"
    "            }\n"
    "          })();\n"
    "        });\n"
    "        captureRef.current = capture;\n"
    "        await capture.start(selectedDevice);\n",
)

test_path = root / "apps/server/tests/test_runtime_lifecycle.py"
text = test_path.read_text(encoding="utf-8")
marker = "test_runtime_threads_configured_audio_frame_limit_into_vad"
if marker not in text:
    text = text.rstrip() + "\n\n\n" + '''def test_runtime_threads_configured_audio_frame_limit_into_vad(tmp_path) -> None:\n    settings = Settings(\n        database_path=str(tmp_path / "runtime.db"),\n        max_audio_frame_bytes=2 * 1024 * 1024,\n    )\n    runtime = CaptionRuntime(settings)\n    assert runtime._vad.max_frame_bytes == settings.max_audio_frame_bytes\n''' + "\n"
    test_path.write_text(text, encoding="utf-8")

Path(__file__).unlink()
