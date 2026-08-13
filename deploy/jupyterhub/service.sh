#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$DEPLOY_DIR/../.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run/jupyterhub"
PID_FILE="$RUN_DIR/service.pid"
MODE_FILE="$RUN_DIR/process-group"
LOG_FILE="$RUN_DIR/service.log"
PYTHON_BIN="$PROJECT_ROOT/backend/.venv/bin/python"
ENV_FILE="$PROJECT_ROOT/.env"

usage() {
  echo "사용법: bash deploy/jupyterhub/service.sh {start|stop|restart|status|logs}"
}

read_pid() {
  [[ -f "$PID_FILE" ]] && tr -d '[:space:]' < "$PID_FILE" || true
}

is_running() {
  local pid
  pid="$(read_pid)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1

  # 오래된 PID 파일로 관계없는 프로세스를 종료하는 일을 방지합니다.
  if [[ -r "/proc/$pid/cmdline" ]]; then
    tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq "$DEPLOY_DIR/run.sh"
  fi
}

dotenv_value() {
  local name="$1"
  if [[ ! -x "$PYTHON_BIN" ]]; then
    return 0
  fi
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

show_url() {
  local configured discovered
  configured="${PUBLIC_URL:-$(dotenv_value PUBLIC_URL)}"
  if [[ -n "$configured" ]]; then
    echo "외부 URL: $configured"
    return
  fi
  if [[ -f "$LOG_FILE" ]]; then
    discovered="$(grep -Eo 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "$LOG_FILE" | tail -n 1 || true)"
    [[ -n "$discovered" ]] && echo "외부 URL: $discovered"
  fi
}

start_service() {
  if is_running; then
    echo "[service] 이미 실행 중입니다. PID $(read_pid)"
    show_url
    return 0
  fi

  mkdir -p "$RUN_DIR"
  rm -f "$PID_FILE" "$MODE_FILE"
  : > "$LOG_FILE"

  if command -v setsid >/dev/null 2>&1; then
    nohup setsid bash "$DEPLOY_DIR/run.sh" >> "$LOG_FILE" 2>&1 < /dev/null &
    echo "group" > "$MODE_FILE"
  else
    nohup bash "$DEPLOY_DIR/run.sh" >> "$LOG_FILE" 2>&1 < /dev/null &
    echo "single" > "$MODE_FILE"
  fi
  local pid=$!
  echo "$pid" > "$PID_FILE"

  echo "[service] 시작 중: PID $pid"
  for ((attempt = 0; attempt < 180; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[service] 시작 실패" >&2
      tail -n 80 "$LOG_FILE" >&2
      return 1
    fi
    if grep -q "Cloudflare .*Tunnel 시작" "$LOG_FILE"; then
      break
    fi
    sleep 0.5
  done

  local configured_url tunnel_token
  configured_url="${PUBLIC_URL:-$(dotenv_value PUBLIC_URL)}"
  tunnel_token="${CLOUDFLARE_TUNNEL_TOKEN:-$(dotenv_value CLOUDFLARE_TUNNEL_TOKEN)}"
  if [[ -z "$configured_url" && -z "$tunnel_token" ]]; then
    # Quick Tunnel URL은 cloudflared가 시작된 뒤 몇 초 후 로그에 나타납니다.
    for ((attempt = 0; attempt < 60; attempt++)); do
      grep -Eq 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "$LOG_FILE" && break
      sleep 0.5
    done
  fi

  echo "[service] 실행 완료"
  show_url
  echo "로그: $LOG_FILE"
}

stop_service() {
  if ! is_running; then
    echo "[service] 실행 중이 아닙니다."
    rm -f "$PID_FILE" "$MODE_FILE"
    return 0
  fi

  local pid mode
  pid="$(read_pid)"
  mode="$(cat "$MODE_FILE" 2>/dev/null || echo single)"
  echo "[service] 종료 중: PID $pid"
  if [[ "$mode" == "group" ]]; then
    kill -TERM -- "-$pid" 2>/dev/null || true
  else
    kill -TERM "$pid" 2>/dev/null || true
  fi

  for ((attempt = 0; attempt < 100; attempt++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "[service] 정상 종료 시간이 지나 강제 종료합니다." >&2
    if [[ "$mode" == "group" ]]; then
      kill -KILL -- "-$pid" 2>/dev/null || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE" "$MODE_FILE"
  echo "[service] 종료 완료"
}

status_service() {
  if ! is_running; then
    echo "[service] 중지됨"
    return 1
  fi

  local port
  port="${PORT:-$(dotenv_value PORT)}"
  port="${port:-8000}"
  echo "[service] 실행 중: PID $(read_pid)"
  if curl --fail --silent "http://127.0.0.1:$port/health" >/dev/null; then
    echo "FastAPI: 정상"
  else
    echo "FastAPI: 응답 없음"
  fi
  show_url
}

action="${1:-}"
case "$action" in
  start) start_service ;;
  stop) stop_service ;;
  restart) stop_service; start_service ;;
  status) status_service ;;
  logs)
    mkdir -p "$RUN_DIR"
    touch "$LOG_FILE"
    tail -n 100 -f "$LOG_FILE"
    ;;
  *) usage >&2; exit 2 ;;
esac
