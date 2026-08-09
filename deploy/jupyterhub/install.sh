#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$DEPLOY_DIR/../.." && pwd)"
VENV_DIR="$PROJECT_ROOT/backend/.venv"
TOOLS_DIR="$PROJECT_ROOT/.tools"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[install] 필요한 명령을 찾을 수 없습니다: $1" >&2
    exit 1
  fi
}

require_command python3
require_command git
require_command node
require_command npm
require_command curl

python3 - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit("[install] Python 3.11 이상이 필요합니다.")
print(f"[install] Python {sys.version.split()[0]}")
PY

node - <<'JS'
const major = Number(process.versions.node.split('.')[0])
if (major < 20) {
  console.error('[install] Node.js 20 이상이 필요합니다.')
  process.exit(1)
}
console.log(`[install] Node.js ${process.versions.node}`)
JS

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[install] Python 가상환경 생성"
  if ! python3 -m venv "$VENV_DIR"; then
    echo "[install] 가상환경 생성 실패: 학교 관리자에게 python3-venv 설치를 요청하세요." >&2
    exit 1
  fi
fi

PYTHON_BIN="$VENV_DIR/bin/python"
echo "[install] 백엔드 의존성 설치"
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$PROJECT_ROOT/backend/requirements.txt"

echo "[install] 프론트엔드 의존성 설치 및 production build"
(
  cd "$PROJECT_ROOT/frontend"
  npm ci
  npm run build
)

if command -v cloudflared >/dev/null 2>&1; then
  echo "[install] 시스템 cloudflared 사용: $(command -v cloudflared)"
elif [[ -x "$TOOLS_DIR/cloudflared" ]]; then
  echo "[install] 프로젝트 cloudflared가 이미 설치되어 있습니다."
else
  case "$(uname -m)" in
    x86_64|amd64) cloudflared_arch="amd64" ;;
    aarch64|arm64) cloudflared_arch="arm64" ;;
    *)
      echo "[install] 지원하지 않는 CPU 아키텍처입니다: $(uname -m)" >&2
      exit 1
      ;;
  esac

  mkdir -p "$TOOLS_DIR"
  echo "[install] cloudflared 설치 (sudo 불필요)"
  curl --fail --location --retry 3 \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cloudflared_arch}" \
    --output "$TOOLS_DIR/cloudflared.download"
  mv "$TOOLS_DIR/cloudflared.download" "$TOOLS_DIR/cloudflared"
  chmod 755 "$TOOLS_DIR/cloudflared"
fi

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  cp "$DEPLOY_DIR/.env.example" "$PROJECT_ROOT/.env"
  echo "[install] .env를 생성했습니다. AI 모델 사용 전 값을 확인하세요."
fi

chmod 755 "$DEPLOY_DIR"/*.sh

echo
echo "[install] 완료"
echo "다음 명령: bash deploy/jupyterhub/service.sh start"
