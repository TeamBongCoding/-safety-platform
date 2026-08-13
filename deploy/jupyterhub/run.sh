#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$DEPLOY_DIR/../.." && pwd)"
PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/.env"
APP_PID=""
TUNNEL_PID=""

cleanup() {
  trap - EXIT INT TERM
  [[ -n "$TUNNEL_PID" ]] && kill -TERM "$TUNNEL_PID" 2>/dev/null || true
  [[ -n "$APP_PID"    ]] && kill -TERM "$APP_PID"     2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

dotenv_value() {
  local name="$1"
  "$PYTHON_BIN" - "$ENV_FILE" "$name" <<'PY'
import os
import sys
from dotenv import dotenv_values

path, name = sys.argv[1:]
value = os.environ.get(name)
if value is None and os.path.isfile(path):
    value = dotenv_values(path).get(name)
print(value or "")
PY
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[run] 가상환경이 없습니다. install.sh를 먼저 실행하세요." >&2
  exit 1
fi
if [[ ! -f "$PROJECT_ROOT/frontend/dist/index.html" ]]; then
  echo "[run] 프론트엔드 build가 없습니다. install.sh를 먼저 실행하세요." >&2
  exit 1
fi

if command -v cloudflared >/dev/null 2>&1; then
  CLOUDFLARED_BIN="$(command -v cloudflared)"
elif [[ -x "$PROJECT_ROOT/.tools/cloudflared" ]]; then
  CLOUDFLARED_BIN="$PROJECT_ROOT/.tools/cloudflared"
else
  echo "[run] cloudflared가 없습니다. install.sh를 먼저 실행하세요." >&2
  exit 1
fi

PORT="${PORT:-$(dotenv_value PORT)}"
PORT="${PORT:-8000}"
TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-$(dotenv_value CLOUDFLARE_TUNNEL_TOKEN)}"

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "[run] PORT는 1~65535 범위의 숫자여야 합니다: $PORT" >&2
  exit 1
fi

echo "[run] FastAPI 시작: http://127.0.0.1:$PORT"
(
  cd "$PROJECT_ROOT/backend"
  exec "$PYTHON_BIN" -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips 127.0.0.1
) &
APP_PID=$!

app_ready=0
for ((attempt = 0; attempt < 180; attempt++)); do
  if "$PYTHON_BIN" - "$PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=1) as response:
    raise SystemExit(0 if json.load(response).get("status") == "ok" else 1)
PY
  then
    app_ready=1
    break
  fi
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    wait "$APP_PID"
    exit $?
  fi
  sleep 0.5
done

if (( ! app_ready )); then
  echo "[run] FastAPI가 90초 안에 준비되지 않았습니다." >&2
  exit 1
fi

if [[ -n "$TUNNEL_TOKEN" ]]; then
  echo "[run] Cloudflare named tunnel 시작"
  TUNNEL_TOKEN="$TUNNEL_TOKEN" \
    "$CLOUDFLARED_BIN" tunnel --no-autoupdate run &
else
  echo "[run] Cloudflare Quick Tunnel 시작 (재시작할 때 URL이 바뀝니다)"
  "$CLOUDFLARED_BIN" tunnel --no-autoupdate \
    --protocol http2 \
    --url "http://127.0.0.1:$PORT" &
fi
TUNNEL_PID=$!

set +e
wait -n "$APP_PID" "$TUNNEL_PID"
status=$?
set -e
exit "$status"
