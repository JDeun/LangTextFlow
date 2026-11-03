#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d /tmp/ltf-e2e-data-XXXXXX)"
export LANGTEXTFLOW_DATABASE_PATH="${LANGTEXTFLOW_DATABASE_PATH:-$test_root/events.db}"
export LANGTEXTFLOW_MODEL_CACHE_DIR="${LANGTEXTFLOW_MODEL_CACHE_DIR:-$test_root/models}"
log_dir="${LTF_E2E_LOG_DIR:-$test_root}"
mkdir -p "$log_dir"

# Launch the interpreter directly so cleanup owns the server PID, not a uv wrapper.
python_bin="$(uv run python -c 'import sys; print(sys.executable)')"
api_pid=""
web_pid=""
provider_pid=""
chrome_bin="$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)"
if [[ -z "$chrome_bin" && -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then
  chrome_bin="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi

chrome_pid=""
chrome_profile="$(mktemp -d /tmp/ltf-e2e-chrome-XXXXXX)"
cleanup() {
  local status=$?
  if (( status != 0 )); then
    echo "Browser smoke failed; last server state and service logs:" >&2
    curl --fail --silent --max-time 2 http://127.0.0.1:8000/api/v1/state >&2 || true
    tail -n 60 "$log_dir/api.log" "$log_dir/web.log" "$log_dir/chrome.log" "$log_dir/provider.log" >&2 || true
  fi
  local pids=()
  for pid in "$web_pid" "$api_pid" "$provider_pid"; do
    if [[ -n "$pid" ]]; then pids+=("$pid"); fi
  done
  if [[ -n "$chrome_pid" ]]; then pids+=("$chrome_pid"); fi
  if (( ${#pids[@]} > 0 )); then
    kill "${pids[@]}" 2>/dev/null || true
    # A wedged HTTP request can prevent uvicorn's graceful shutdown indefinitely.
    for _ in $(seq 1 50); do
      local alive=false
      for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then alive=true; fi
      done
      if [[ "$alive" == false ]]; then break; fi
      sleep 0.1
    done
    kill -KILL "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
  rm -rf -- "$chrome_profile"
  rm -rf -- "$test_root"
  return "$status"
}
trap cleanup EXIT

if [[ -z "$chrome_bin" ]]; then
  echo "Chrome/Chromium not found on runner" >&2
  exit 1
fi

# Exercise the real summary job/API against a synthetic local provider, never a
# developer's Ollama instance or remote model. Each run gets an OS-assigned port.
"$python_bin" scripts/e2e_ollama_stub.py "$test_root/provider-port" >"$log_dir/provider.log" 2>&1 &
provider_pid=$!
for _ in $(seq 1 50); do
  if [[ -s "$test_root/provider-port" ]]; then break; fi
  sleep 0.1
done
if [[ ! -s "$test_root/provider-port" ]]; then
  echo "Synthetic summary provider failed to start" >&2
  exit 1
fi
read -r provider_port < "$test_root/provider-port"
export LANGTEXTFLOW_OLLAMA_URL="http://127.0.0.1:$provider_port"
export LTF_E2E_OLLAMA_URL="$LANGTEXTFLOW_OLLAMA_URL"
"$python_bin" -m uvicorn langtextflow.main:app --app-dir apps/server --host 127.0.0.1 --port 8000 >"$log_dir/api.log" 2>&1 &
api_pid=$!
(
  cd apps/web
  exec node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 5173 --strictPort >"$log_dir/web.log" 2>&1
) &
web_pid=$!

start_chrome() {
  "$chrome_bin" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --no-first-run --no-default-browser-check --disable-extensions \
    --remote-debugging-port=9222 --user-data-dir="$chrome_profile" about:blank \
    >"$log_dir/chrome.log" 2>&1 &
  chrome_pid=$!
}

ready_chrome() {
  for _ in $(seq 1 80); do
    if curl --fail --silent --max-time 2 http://127.0.0.1:9222/json/version >/dev/null; then
      return 0
    fi
    if ! kill -0 "$chrome_pid" 2>/dev/null; then
      return 1
    fi
    sleep 0.5
  done
  return 1
}

# Never race the first CDP request or silently run smoke tests when a service
# failed to start. A fresh profile and bounded restart handle stale/orphaned
# Chrome on heavily loaded Actions runners.
for attempt in 1 2; do
  start_chrome
  if ready_chrome; then break; fi
  echo "Chromium DevTools unavailable (attempt $attempt)" >&2
  tail -n 35 "$log_dir/chrome.log" >&2 || true
  kill "$chrome_pid" 2>/dev/null || true
  wait "$chrome_pid" 2>/dev/null || true
  chrome_pid=""
  if (( attempt == 2 )); then exit 1; fi
  rm -rf -- "$chrome_profile"
  chrome_profile="$(mktemp -d /tmp/ltf-e2e-chrome-XXXXXX)"
done

for _ in $(seq 1 80); do
  if [[ $(<"$log_dir/web.log") == *"Local:"* ]] \
    && curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null \
    && curl --fail --silent --max-time 2 http://127.0.0.1:5173/ >/dev/null; then
    break
  fi
  sleep 0.5
done
if ! curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null \
   || ! curl --fail --silent --max-time 2 http://127.0.0.1:5173/ >/dev/null; then
  echo "E2E API/web services failed readiness checks" >&2
  tail -n 30 "$log_dir/api.log" "$log_dir/web.log" >&2 || true
  exit 1
fi

node apps/web/tests/e2e-smoke.mjs
node apps/web/tests/operator-session-smoke.mjs
node apps/web/tests/focus-smoke.mjs
node apps/web/tests/localization-layout-smoke.mjs
node apps/web/tests/a11y-smoke.mjs
