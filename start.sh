#!/usr/bin/env bash

set -Eeuo pipefail

WITH_ANALYZER=0

usage() {
  cat <<'EOF'
Usage: ./start.sh [options]

Options:
  --with-analyzer  Enable live AI analysis (ANALYSIS_ENABLED=1)
  -h, --help       Show this help
EOF
}

for argument in "$@"; do
  case "$argument" in
    --with-analyzer) WITH_ANALYZER=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
ENV_FILE="$PROJECT_ROOT/.env"

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

load_env_file() {
  [[ -f "$ENV_FILE" ]] || return 0

  local raw line name value
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    line="${raw%$'\r'}"
    line="$(trim "$line")"
    [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *"="* ]] && continue

    name="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] ||
         [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi

    if [[ ! -v "$name" ]]; then
      printf -v "$name" '%s' "$value"
      export "$name"
    fi
  done < "$ENV_FILE"
}

find_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || [[ -x "$PYTHON_BIN" ]] || {
      echo "PYTHON_BIN을 실행할 수 없습니다: $PYTHON_BIN" >&2
      exit 1
    }
    return
  fi

  if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
  elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "Python 3을 찾을 수 없습니다." >&2
    exit 1
  fi
}

load_env_file
find_python

# 이전 실행에서 남은 프로세스 정리 (lsof가 없거나 동작 안 할 때를 위해 /proc/net/tcp로 직접 탐색)
kill_port() {
  local port="$1"
  "$PYTHON_BIN" - "$port" <<'PYEOF' 2>/dev/null || true
import sys, os, glob
port = int(sys.argv[1])
try:
    with open('/proc/net/tcp') as f:
        for line in f.readlines()[1:]:
            parts = line.split()
            if int(parts[1].split(':')[1], 16) == port:
                inode = parts[9]
                for pid_dir in glob.glob('/proc/[0-9]*/fd'):
                    pid = pid_dir.split('/')[2]
                    try:
                        for fd in os.listdir(pid_dir):
                            if f'socket:[{inode}]' in os.readlink(f'{pid_dir}/{fd}'):
                                os.kill(int(pid), 15)
                    except: pass
except: pass
PYEOF
}

for port in 8000 5173 3000; do
  kill_port "$port"
done
sleep 0.5

command -v npm >/dev/null 2>&1 || {
  echo "npm을 찾을 수 없습니다. Node.js와 npm을 설치하세요." >&2
  exit 1
}

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_HEALTH_HOST="$BACKEND_HOST"
[[ "$BACKEND_HEALTH_HOST" == "0.0.0.0" ]] && BACKEND_HEALTH_HOST="127.0.0.1"
BACKEND_HEALTH_URL="http://${BACKEND_HEALTH_HOST}:${BACKEND_PORT}/health"

if (( WITH_ANALYZER )); then
  export ANALYSIS_ENABLED=1
  export POSE_ENABLED=1
fi

PIDS=()
PROCESS_GROUPS=()
LAST_PID=""
CLEANED_UP=0

start_component() {
  local title="$1"
  local working_directory="$2"
  shift 2

  echo "[$title] 시작"
  if command -v setsid >/dev/null 2>&1; then
    (
      cd "$working_directory"
      exec setsid "$@"
    ) &
    PROCESS_GROUPS+=(1)
  else
    (
      cd "$working_directory"
      exec "$@"
    ) &
    PROCESS_GROUPS+=(0)
  fi
  LAST_PID=$!
  PIDS+=("$LAST_PID")
}

cleanup() {
  (( CLEANED_UP )) && return 0
  CLEANED_UP=1
  echo
  echo "프로세스를 종료합니다."

  local index pid
  for index in "${!PIDS[@]}"; do
    pid="${PIDS[$index]}"
    if kill -0 "$pid" 2>/dev/null; then
      if [[ "${PROCESS_GROUPS[$index]}" == "1" ]]; then
        kill -TERM -- "-$pid" 2>/dev/null || true
      else
        kill -TERM "$pid" 2>/dev/null || true
      fi
    fi
  done

  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}

trap cleanup EXIT INT TERM

start_component \
  "Backend" \
  "$BACKEND_DIR" \
  "$PYTHON_BIN" -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
BACKEND_PID="$LAST_PID"

echo "[Backend] 준비 상태 확인 중..."
backend_ready=0
for ((attempt = 0; attempt < 180; attempt++)); do
  if "$PYTHON_BIN" -c \
    'import json, sys, urllib.request; data=json.load(urllib.request.urlopen(sys.argv[1], timeout=1)); raise SystemExit(0 if data.get("status") == "ok" else 1)' \
    "$BACKEND_HEALTH_URL" >/dev/null 2>&1; then
    backend_ready=1
    break
  fi

  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "백엔드가 준비되기 전에 종료되었습니다. 위 오류를 확인하세요." >&2
    exit 1
  fi
  sleep 0.5
done

if (( ! backend_ready )); then
  echo "90초 안에 백엔드가 시작되지 않았습니다." >&2
  exit 1
fi
echo "[Backend] 준비 완료"

start_component \
  "Frontend" \
  "$FRONTEND_DIR" \
  npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"

echo
echo "======================================"
echo " 실행 완료"
echo "======================================"

SERVICE_PREFIX="${JUPYTERHUB_SERVICE_PREFIX:-}"
HUB="${HUB_PUBLIC_URL:-}"

if [[ -n "$SERVICE_PREFIX" && -n "$HUB" ]]; then
  echo " 개발서버(HMR) : ${HUB}${SERVICE_PREFIX}proxy/${FRONTEND_PORT}/"
  echo " 빌드버전      : ${HUB}${SERVICE_PREFIX}proxy/${BACKEND_PORT}/"
  echo " API 문서      : ${HUB}${SERVICE_PREFIX}proxy/${BACKEND_PORT}/docs"
else
  echo " 개발서버(HMR) : http://localhost:${FRONTEND_PORT}/"
  echo " 빌드버전      : http://localhost:${BACKEND_PORT}/"
  echo " API 문서      : http://localhost:${BACKEND_PORT}/docs"
fi

echo "======================================"
echo " 종료: Ctrl+C"
echo "======================================"

wait -n "${PIDS[@]}"
