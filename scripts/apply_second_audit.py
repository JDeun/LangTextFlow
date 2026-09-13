from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, path: str) -> str:
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:80]!r}")
    return content.replace(old, new, 1)


def patch_main() -> None:
    path = "apps/server/langtextflow/main.py"
    content = read(path)
    cors_block = '''app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
'''
    middleware = cors_block + '''

@app.middleware("http")
async def reject_remote_operator_api_before_body_parse(request: Request, call_next):
    """Reject LAN access to operator REST APIs before request-body parsing.

    Audience read-only endpoints intentionally remain reachable on the LAN. All
    other /api/v1 routes are operator-only and must be rejected at the ASGI
    middleware boundary so an untrusted LAN peer cannot spend CPU/memory on
    Pydantic parsing before receiving a 403.
    """
    path = request.url.path
    is_audience_path = path.startswith("/api/v1/audience/")
    if path.startswith("/api/v1/") and not is_audience_path:
        host = request.client.host if request.client else None
        if not is_loopback_client(host):
            return Response(
                content='{"detail":"operator API is local-only"}',
                status_code=403,
                media_type="application/json",
            )
    return await call_next(request)
'''
    if "reject_remote_operator_api_before_body_parse" not in content:
        content = replace_once(content, cors_block, middleware, path=path)

    old = '''            if frame is not None:
                await runtime.feed_audio(frame)
'''
    new = '''            if frame is not None:
                if len(frame) > settings.max_audio_frame_bytes:
                    await websocket.close(
                        code=1009,
                        reason="audio frame exceeds configured safety limit",
                    )
                    break
                await runtime.feed_audio(frame)
'''
    if "audio frame exceeds configured safety limit" not in content:
        content = replace_once(content, old, new, path=path)
    write(path, content)


def patch_telemetry_runtime() -> None:
    telemetry_path = "apps/server/langtextflow/telemetry.py"
    telemetry = read(telemetry_path)
    old = '''    def __init__(self, threshold_dbfs: float = -45.0, hangover_frames: int = 3) -> None:
        self.threshold_dbfs = threshold_dbfs
        self.hangover_frames = max(0, hangover_frames)
        self._hangover = 0
'''
    new = '''    def __init__(
        self,
        threshold_dbfs: float = -45.0,
        hangover_frames: int = 3,
        max_frame_bytes: int = MAX_PCM_FRAME_BYTES,
    ) -> None:
        self.threshold_dbfs = threshold_dbfs
        self.hangover_frames = max(0, hangover_frames)
        self.max_frame_bytes = max(4, max_frame_bytes)
        self._hangover = 0
'''
    if "self.max_frame_bytes = max(4, max_frame_bytes)" not in telemetry:
        telemetry = replace_once(telemetry, old, new, path=telemetry_path)
        telemetry = replace_once(
            telemetry,
            "        samples = decode_pcm_f32le(pcm_f32le)\n",
            "        samples = decode_pcm_f32le(\n            pcm_f32le, max_frame_bytes=self.max_frame_bytes\n        )\n",
            path=telemetry_path,
        )
    write(telemetry_path, telemetry)

    runtime_path = "apps/server/langtextflow/runtime.py"
    runtime = read(runtime_path)
    old_runtime = '''        self._vad = EnergyVad(
            threshold_dbfs=self.settings.vad_threshold_dbfs,
            hangover_frames=self.settings.vad_hangover_frames,
        )
'''
    new_runtime = '''        self._vad = EnergyVad(
            threshold_dbfs=self.settings.vad_threshold_dbfs,
            hangover_frames=self.settings.vad_hangover_frames,
            max_frame_bytes=self.settings.max_audio_frame_bytes,
        )
'''
    if "max_frame_bytes=self.settings.max_audio_frame_bytes" not in runtime:
        runtime = replace_once(runtime, old_runtime, new_runtime, path=runtime_path)
    write(runtime_path, runtime)


def patch_config() -> None:
    path = "apps/server/langtextflow/config.py"
    content = read(path)
    if "from pydantic import Field, field_validator" not in content:
        content = content.replace(
            "from pydantic_settings import BaseSettings, SettingsConfigDict\n",
            "from pydantic import Field, field_validator\nfrom pydantic_settings import BaseSettings, SettingsConfigDict\n",
            1,
        )
    replacements = {
        '    frontend_port: int = 5173\n': '    frontend_port: int = Field(default=5173, ge=1, le=65535)\n',
        '    backend_port: int = 8000\n': '    backend_port: int = Field(default=8000, ge=1, le=65535)\n',
        '    max_segments: int = 100\n': '    max_segments: int = Field(default=100, ge=1, le=10_000)\n',
        '    audience_join_max_failures: int = 8\n': '    audience_join_max_failures: int = Field(default=8, ge=1, le=10_000)\n',
        '    audience_join_window_seconds: float = 60.0\n': '    audience_join_window_seconds: float = Field(default=60.0, gt=0, le=86_400)\n',
        '    audience_join_block_seconds: float = 300.0\n': '    audience_join_block_seconds: float = Field(default=300.0, gt=0, le=86_400)\n',
        '    audience_join_max_tracked_clients: int = 4096\n': '    audience_join_max_tracked_clients: int = Field(default=4096, ge=1, le=1_000_000)\n',
        '    vibevoice_tensor_parallel_size: int = 1\n': '    vibevoice_tensor_parallel_size: int = Field(default=1, ge=1, le=64)\n',
        '    vibevoice_max_model_len: int = 16384\n': '    vibevoice_max_model_len: int = Field(default=16384, ge=1)\n',
        '    vibevoice_max_audio_windows: int = 512\n': '    vibevoice_max_audio_windows: int = Field(default=512, ge=1)\n',
        '    vibevoice_mm_processor_cache_gb: float = 16.0\n': '    vibevoice_mm_processor_cache_gb: float = Field(default=16.0, ge=0)\n',
        '    vibevoice_gpu_memory_utilization: float = 0.85\n': '    vibevoice_gpu_memory_utilization: float = Field(default=0.85, gt=0, le=1)\n',
        '    vibevoice_startup_timeout_seconds: float = 600.0\n': '    vibevoice_startup_timeout_seconds: float = Field(default=600.0, gt=0)\n',
        '    audio_queue_chunks: int = 32\n': '    audio_queue_chunks: int = Field(default=32, ge=1, le=4096)\n',
        '    max_audio_frame_bytes: int = 1024 * 1024\n': '    max_audio_frame_bytes: int = Field(default=1024 * 1024, ge=4, le=8 * 1024 * 1024)\n',
        '    asr_replay_seconds: float = 8.0\n': '    asr_replay_seconds: float = Field(default=8.0, ge=0, le=120)\n',
        '    asr_health_check_seconds: float = 0.25\n': '    asr_health_check_seconds: float = Field(default=0.25, gt=0, le=60)\n',
        '    faster_whisper_chunk_seconds: float = 4.0\n': '    faster_whisper_chunk_seconds: float = Field(default=4.0, gt=0, le=120)\n',
        '    vad_hangover_frames: int = 3\n': '    vad_hangover_frames: int = Field(default=3, ge=0, le=10_000)\n',
        '    audio_backpressure_warn_ms: float = 50.0\n': '    audio_backpressure_warn_ms: float = Field(default=50.0, ge=0)\n',
        '    correction_timeout_seconds: float = 4.0\n': '    correction_timeout_seconds: float = Field(default=4.0, gt=0)\n',
        '    openai_compatible_timeout_seconds: float = 20.0\n': '    openai_compatible_timeout_seconds: float = Field(default=20.0, gt=0)\n',
    }
    for old, new in replacements.items():
        if old in content:
            content = content.replace(old, new, 1)

    marker = '''    model_config = SettingsConfigDict(
'''
    validator = '''    @field_validator("max_audio_frame_bytes")
    @classmethod
    def audio_frame_bytes_must_align_to_float32(cls, value: int) -> int:
        if value % 4:
            raise ValueError("max_audio_frame_bytes must be divisible by 4")
        return value

'''
    if "audio_frame_bytes_must_align_to_float32" not in content:
        content = replace_once(content, marker, validator + marker, path=path)
    write(path, content)


def patch_tests() -> None:
    path = "apps/server/tests/test_api_boundaries.py"
    content = read(path)
    content = content.replace(
        "from langtextflow.models import SessionContext, SessionState\n",
        "from langtextflow.models import AudioStreamInfo, SessionContext, SessionState\n",
        1,
    )
    additions = '''

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
'''
    if "test_remote_operator_request_is_rejected_before_json_body_parsing" not in content:
        content += additions
    write(path, content)

    write(
        "apps/server/tests/test_config.py",
        '''from __future__ import annotations

import pytest
from pydantic import ValidationError

from langtextflow.config import Settings


def test_settings_reject_invalid_ports() -> None:
    with pytest.raises(ValidationError):
        Settings(backend_port=0)
    with pytest.raises(ValidationError):
        Settings(frontend_port=65536)


def test_settings_reject_unaligned_audio_frame_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(max_audio_frame_bytes=1025)


def test_settings_accept_small_aligned_audio_frame_limit() -> None:
    settings = Settings(max_audio_frame_bytes=4096)
    assert settings.max_audio_frame_bytes == 4096
''',
    )

    write(
        "apps/server/tests/test_property_boundaries.py",
        '''from __future__ import annotations

import math

from hypothesis import given, settings, strategies as st

from langtextflow.telemetry import decode_pcm_f32le


@settings(max_examples=100, deadline=None)
@given(st.binary(max_size=4096))
def test_pcm_decoder_never_returns_non_finite_samples(payload: bytes) -> None:
    try:
        samples = decode_pcm_f32le(payload, max_frame_bytes=4096)
    except ValueError:
        return
    assert all(math.isfinite(value) for value in samples)
    assert all(abs(value) <= 4.0 for value in samples)


@settings(max_examples=100, deadline=None)
@given(st.integers(min_value=1, max_value=2048))
def test_pcm_decoder_rejects_non_float32_alignment(size: int) -> None:
    if size % 4 == 0:
        return
    try:
        decode_pcm_f32le(bytes(size), max_frame_bytes=4096)
    except ValueError:
        return
    raise AssertionError("misaligned float32 payload was accepted")
''',
    )


def patch_pyproject() -> None:
    path = "pyproject.toml"
    content = read(path)
    needle = '  "bandit>=1.7,<2",\n'
    if '"hypothesis>=6,<7"' not in content:
        content = replace_once(
            content,
            needle,
            needle + '  "hypothesis>=6,<7",\n',
            path=path,
        )
    write(path, content)


def patch_package_json() -> None:
    path = ROOT / "apps/web/package.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    dependencies = data["dependencies"]
    dev_names = [
        "@vitejs/plugin-react",
        "@types/qrcode",
        "vite",
        "typescript",
        "@types/react",
        "@types/react-dom",
    ]
    dev_dependencies = data.setdefault("devDependencies", {})
    for name in dev_names:
        if name in dependencies:
            dev_dependencies[name] = dependencies.pop(name)
    data["dependencies"] = dict(sorted(dependencies.items()))
    data["devDependencies"] = dict(sorted(dev_dependencies.items()))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_actions(uv_version: str) -> None:
    checkout = "3d3c42e5aac5ba805825da76410c181273ba90b1"
    setup_python = "13c4551a7ae155362c9a2d2e0d0b55754e39c798"
    setup_node = "820762786026740c76f36085b0efc47a31fe5020"
    upload = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    codeql = "b96794f015dfd88f77b49b1c93e0fa7110f94c63"

    ci_path = ".github/workflows/ci.yml"
    ci = read(ci_path)
    for old, new in {
        "actions/checkout@v7": f"actions/checkout@{checkout} # v7",
        "actions/setup-python@v7": f"actions/setup-python@{setup_python} # v7",
        "actions/setup-node@v7": f"actions/setup-node@{setup_node} # v7",
        "actions/upload-artifact@v7": f"actions/upload-artifact@{upload} # v7",
    }.items():
        ci = ci.replace(old, new)

    ci = ci.replace(
        '''      - name: Upgrade packaging bootstrap tools
        run: python -m pip install --upgrade "pip>=26" "setuptools>=83" wheel
      - run: pip install -e '.[dev]'
      - name: Verify installed dependency consistency
        run: python -m pip check
''',
        f'''      - name: Install pinned uv bootstrap
        run: python -m pip install "uv=={uv_version}"
      - name: Sync locked backend dependencies
        run: uv sync --locked --extra dev
      - name: Verify installed dependency consistency
        run: uv pip check
''',
    )
    for command in [
        "python scripts/repo_hygiene.py",
        "python scripts/check_markdown_links.py",
        "python scripts/check_hf_snapshot_calls.py",
        "ruff check apps/server scripts",
        "pytest -q",
        "pytest --strict-config --strict-markers",
        "python -m uvicorn langtextflow.main:app",
        "pip-audit --format cyclonedx-json --output artifacts/python-sbom.cdx.json",
        "bandit -r apps/server/langtextflow -ll -s B615",
    ]:
        if command in ci and not command.startswith("pytest -q") and not command.startswith("pytest --strict"):
            ci = ci.replace(command, "uv run " + command)
    ci = ci.replace("          pytest -q\n", "          uv run pytest -q\n")
    ci = ci.replace(
        "          pytest --strict-config --strict-markers\n",
        "          uv run pytest --strict-config --strict-markers\n",
    )

    ci = ci.replace(
        "      - name: Install backend test dependencies\n        run: python -m pip install -e '.[dev]'\n",
        f"      - name: Install pinned uv bootstrap\n        run: python -m pip install \"uv=={uv_version}\"\n      - name: Sync locked backend dependencies\n        run: uv sync --locked --extra dev\n",
    )
    ci = ci.replace("          python -m pytest -q\n", "          uv run python -m pytest -q\n")

    browser_job = f'''

  browser-e2e:
    name: Browser E2E smoke
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@{checkout} # v7
      - uses: actions/setup-python@{setup_python} # v7
        with:
          python-version: "3.11"
      - name: Install pinned uv bootstrap
        run: python -m pip install "uv=={uv_version}"
      - name: Sync locked backend dependencies
        run: uv sync --locked --extra dev
      - uses: actions/setup-node@{setup_node} # v7
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: apps/web/package-lock.json
      - name: Install and build frontend
        working-directory: apps/web
        run: |
          npm ci
          npm run build
      - name: End-to-end operator and audience smoke
        env:
          LANGTEXTFLOW_DATABASE_PATH: /tmp/langtextflow-e2e.db
        run: bash scripts/run_browser_e2e.sh
'''
    if "browser-e2e:" not in ci:
        ci = ci.replace("\n  platform-smoke:\n", browser_job + "\n  platform-smoke:\n", 1)
    write(ci_path, ci)

    codeql_path = ".github/workflows/codeql.yml"
    codeql_text = read(codeql_path)
    codeql_text = codeql_text.replace(
        "actions/checkout@v7", f"actions/checkout@{checkout} # v7"
    )
    codeql_text = codeql_text.replace(
        "github/codeql-action/init@v4", f"github/codeql-action/init@{codeql} # v4"
    )
    codeql_text = codeql_text.replace(
        "github/codeql-action/autobuild@v4", f"github/codeql-action/autobuild@{codeql} # v4"
    )
    codeql_text = codeql_text.replace(
        "github/codeql-action/analyze@v4", f"github/codeql-action/analyze@{codeql} # v4"
    )
    write(codeql_path, codeql_text)


def write_browser_e2e() -> None:
    write(
        "scripts/run_browser_e2e.sh",
        '''#!/usr/bin/env bash
set -euo pipefail

uv run python -m uvicorn langtextflow.main:app --app-dir apps/server --host 127.0.0.1 --port 8000 >/tmp/ltf-e2e-api.log 2>&1 &
api_pid=$!
(
  cd apps/web
  npx vite preview --host 127.0.0.1 --port 5173 >/tmp/ltf-e2e-web.log 2>&1
) &
web_pid=$!
chrome_bin="$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)"
if [[ -z "$chrome_bin" ]]; then
  echo "Chrome/Chromium not found on runner" >&2
  exit 1
fi
"$chrome_bin" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --remote-debugging-port=9222 --user-data-dir=/tmp/ltf-chrome about:blank >/tmp/ltf-e2e-chrome.log 2>&1 &
chrome_pid=$!
cleanup() {
  kill "$chrome_pid" "$web_pid" "$api_pid" 2>/dev/null || true
  wait "$chrome_pid" "$web_pid" "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 80); do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null \
    && curl --fail --silent http://127.0.0.1:5173/ >/dev/null \
    && curl --fail --silent http://127.0.0.1:9222/json/version >/dev/null; then
    break
  fi
  sleep 0.25
done

node apps/web/tests/e2e-smoke.mjs
''',
    )
    write(
        "apps/web/tests/e2e-smoke.mjs",
        r'''const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, label, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}${lastError ? `: ${lastError}` : ""}`);
}

const targetResponse = await fetch(
  `http://127.0.0.1:9222/json/new?${encodeURIComponent("http://127.0.0.1:5173/")}`,
  { method: "PUT" },
);
if (!targetResponse.ok) throw new Error(`CDP target creation failed: ${targetResponse.status}`);
const target = await targetResponse.json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
  else waiter.resolve(message.result);
});

function cdp(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "browser evaluation failed");
  }
  return result.result?.value;
}

async function navigate(url) {
  await cdp("Page.navigate", { url });
  await waitFor(() => evaluate("document.readyState === 'complete'"), `page load: ${url}`);
}

await cdp("Page.enable");
await cdp("Runtime.enable");
await navigate("http://127.0.0.1:5173/");
await evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); location.reload(); true`);
await waitFor(
  () => evaluate(`document.readyState === "complete" && document.body.innerText.includes("세션 시작")`),
  "operator UI",
);

const engineChanged = await evaluate(`(() => {
  const select = [...document.querySelectorAll("select")].find((node) =>
    node.closest("label")?.innerText.includes("음성 인식 엔진")
  );
  if (!select) return false;
  select.value = "mock";
  select.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
})()`);
if (!engineChanged) throw new Error("ASR engine selector not found");

const startClicked = await evaluate(`(() => {
  const button = [...document.querySelectorAll("button")].find((node) => node.innerText.includes("세션 시작"));
  if (!button || button.disabled) return false;
  button.click();
  return true;
})()`);
if (!startClicked) throw new Error("session start button was not clickable");

await waitFor(
  () => evaluate(`[...document.querySelectorAll("button")].some((node) => node.innerText.includes("자막 중지"))`),
  "session start",
);

const stateResponse = await fetch("http://127.0.0.1:8000/api/v1/state");
if (!stateResponse.ok) throw new Error(`state request failed: ${stateResponse.status}`);
const state = await stateResponse.json();
if (!state.running || !state.join_code) throw new Error(`invalid running state: ${JSON.stringify(state)}`);

await navigate(`http://127.0.0.1:5173/audience/${state.join_code}`);
await waitFor(
  () => evaluate(`document.body.innerText.includes("새 실시간 자막 세션") && document.body.innerText.includes("LIVE")`),
  "audience live view",
);

const stopResponse = await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
if (!stopResponse.ok) throw new Error(`session stop failed: ${stopResponse.status}`);
const stopped = await stopResponse.json();
if (stopped.running) throw new Error("session remained running after stop");

socket.close();
console.log("browser E2E smoke passed");
''',
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uv-version", required=True)
    args = parser.parse_args()
    patch_main()
    patch_telemetry_runtime()
    patch_config()
    patch_tests()
    patch_pyproject()
    patch_package_json()
    patch_actions(args.uv_version)
    write_browser_e2e()


if __name__ == "__main__":
    main()
