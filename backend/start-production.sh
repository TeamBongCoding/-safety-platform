#!/usr/bin/env bash
set -Eeuo pipefail

APP_PID=""
TUNNEL_PID=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$APP_PID" ]]; then
    kill -TERM "$APP_PID" 2>/dev/null || true
  fi
  if [[ -n "$TUNNEL_PID" ]]; then
    kill -TERM "$TUNNEL_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

require_file() {
  local label="$1"
  local path="$2"
  if [[ ! -f "$path" ]]; then
    echo "[startup] Missing $label: $path" >&2
    return 1
  fi
}

if [[ "${ANALYSIS_ENABLED:-0}" == "1" ]]; then
  missing=0
  require_file "helmet model" "${HELMET_MODEL_PATH:-weights/best.pt}" || missing=1
  require_file "person model" "${PERSON_MODEL_PATH:-yolov8n.pt}" || missing=1
  if [[ "${REID_BACKEND:-fastreid}" == "fastreid" ]]; then
    require_file \
      "FastReID weights" \
      "${FASTREID_WEIGHTS_PATH:-weights/market_bot_R50.pth}" || missing=1
  fi
  if (( missing )); then
    echo "[startup] Upload the ignored model files to the mounted RunPod volume." >&2
    exit 1
  fi

  python - <<'PY'
import os
import torch

requested = os.getenv("REID_DEVICE", "auto").lower()
available = torch.cuda.is_available()
print(f"[startup] CUDA available: {available}", flush=True)
if requested.startswith("cuda") and not available:
    raise SystemExit("REID_DEVICE requests CUDA, but this container cannot see a GPU")
PY
fi

python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 &
APP_PID=$!

if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  cloudflared tunnel --no-autoupdate run \
    --token "$CLOUDFLARE_TUNNEL_TOKEN" &
  TUNNEL_PID=$!
  echo "[startup] FastAPI and Cloudflare Tunnel started."
else
  echo "[startup] CLOUDFLARE_TUNNEL_TOKEN is empty; FastAPI started without a tunnel."
fi

pids=("$APP_PID")
if [[ -n "$TUNNEL_PID" ]]; then
  pids+=("$TUNNEL_PID")
fi

set +e
wait -n "${pids[@]}"
status=$?
set -e
exit "$status"
