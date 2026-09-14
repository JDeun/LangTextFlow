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
node apps/web/tests/focus-smoke.mjs
node apps/web/tests/a11y-smoke.mjs
