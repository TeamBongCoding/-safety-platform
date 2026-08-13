#!/usr/bin/env bash

set -Eeuo pipefail

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$DEPLOY_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "[update] 팀 배포 계정의 tracked 파일에 변경 사항이 있습니다." >&2
  echo "[update] 변경을 commit/stash한 뒤 다시 실행하세요." >&2
  git status --short >&2
  exit 1
fi

echo "[update] GitHub 최신 코드 받기"
git pull --ff-only

echo "[update] 의존성 동기화 및 프론트엔드 build"
bash "$DEPLOY_DIR/install.sh"

echo "[update] 서비스 재시작"
bash "$DEPLOY_DIR/service.sh" restart
