#!/usr/bin/env bash
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

chrome_pid=""
chrome_profile="$(mktemp -d /tmp/ltf-e2e-chrome-XXXXXX)"
cleanup() {
  if [[ -n "$chrome_pid" ]]; then kill "$chrome_pid" 2>/dev/null || true; fi
  kill "$web_pid" "$api_pid" 2>/dev/null || true
  wait "$chrome_pid" "$web_pid" "$api_pid" 2>/dev/null || true
  rm -rf -- "$chrome_profile"
}
trap cleanup EXIT

start_chrome() {
  "$chrome_bin" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --no-first-run --no-default-browser-check --disable-extensions \
    --remote-debugging-port=9222 --user-data-dir="$chrome_profile" about:blank \
    >/tmp/ltf-e2e-chrome.log 2>&1 &
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
  tail -n 35 /tmp/ltf-e2e-chrome.log >&2 || true
  kill "$chrome_pid" 2>/dev/null || true
  wait "$chrome_pid" 2>/dev/null || true
  chrome_pid=""
  if (( attempt == 2 )); then exit 1; fi
  rm -rf -- "$chrome_profile"
  chrome_profile="$(mktemp -d /tmp/ltf-e2e-chrome-XXXXXX)"
done

for _ in $(seq 1 80); do
  if curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null \
    && curl --fail --silent --max-time 2 http://127.0.0.1:5173/ >/dev/null; then
    break
  fi
  sleep 0.5
done
if ! curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null \
   || ! curl --fail --silent --max-time 2 http://127.0.0.1:5173/ >/dev/null; then
  echo "E2E API/web services failed readiness checks" >&2
  tail -n 30 /tmp/ltf-e2e-api.log /tmp/ltf-e2e-web.log >&2 || true
  exit 1
fi

node apps/web/tests/e2e-smoke.mjs
node apps/web/tests/focus-smoke.mjs
node apps/web/tests/localization-layout-smoke.mjs
node apps/web/tests/a11y-smoke.mjs
