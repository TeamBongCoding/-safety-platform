# 팀 배포 매뉴얼

> 「AI화성 챌린지」 in 수원대학교 — 팀 배포 계정 활용 가이드

평소 코딩, 노트북 작업과 AI 학습·실험은 개인 개발 매뉴얼에 따라 본인 학번 계정에서
진행합니다. 이 문서는 팀 배포 계정을 이용한 공유 백엔드 영속 실행, DB 보관과 외부 데모
공개만 다룹니다.

## 1. 팀 배포 계정이란

해커톤 단계에서 팀당 하나씩 발급되는 `hwasungteam<N>` 계정입니다.

- 학생 개인 계정과 분리된 별도 컨테이너입니다.
- 무활동으로 종료되지 않는 영속 컨테이너이므로 백엔드를 계속 실행할 수 있습니다.
- 모든 팀원이 같은 ID로 로그인하며 같은 작업 폴더를 공유합니다.
- `cloudflared`로 외부 HTTPS 데모 URL을 공개할 수 있습니다.
- V100 8장 GPU 풀은 학생 계정과 동일하게 노출됩니다.
- DB는 SQLite를 권장하며 필요하면 Supabase·Neon 같은 외부 매니지드 DB를 사용할 수 있습니다.
- 이 챌린지에서는 팀별 MariaDB를 제공하지 않습니다.

팀 배포 계정은 공유 백엔드, DB와 외부 공개 용도로만 사용하세요. 코드 작성, 노트북 실험과
모델 학습은 개인 개발 계정에서 수행한 뒤 GitHub를 통해 배포합니다.

## 2. 로그인

학교에서 안내한 JupyterHub 주소에 접속하여 다음 정보로 로그인합니다.

- ID: 팀의 `hwasungteam<번호>` (예: `hwasungteam7`)
- 초기 비밀번호: 팀장 학번에 등록된 휴대전화 번호 — 정확한 값은 분반 마스터 안내 확인

팀장은 첫 로그인 직후 비밀번호를 변경하고 팀원에게 안전한 채널로 공유합니다. 카카오톡
단체방의 공개 메시지처럼 기록이 쉽게 노출되는 곳에 비밀번호를 평문으로 올리지 마세요.
팀 계정 비밀번호가 노출되면 소스, DB와 외부 데모가 함께 도용될 수 있습니다.

## 3. 배포 구조

이 프로젝트의 유일한 배포 대상은 학교에서 제공하는 `hwasungteam<N>` JupyterHub 컨테이너입니다.
개인 계정에서는 개발과 GitHub push만 하고, 팀 배포 계정에서는 같은 저장소를 pull한 뒤 서비스를
실행합니다. Tailscale, 개인 PC 서버, Docker, RunPod는 사용하지 않습니다.

```text
팀원 A/B/C 개인 개발 계정
  코드 작성 → git commit → git push
                         │
                         ▼
                      GitHub
                         │
                         ▼
hwasungteam<N> 컨테이너의 /home/jovyan/shared/
  git pull → React build → FastAPI(React 포함, 127.0.0.1:8000)
                                      │
                                      ▼
                                  cloudflared
                                      │
                                      ▼
                              외부 HTTPS 데모 URL
```

FastAPI가 React production build도 같은 포트에서 제공합니다. 따라서 API, 로그인 쿠키,
WebSocket 카메라 업로드가 모두 같은 HTTPS origin을 사용하며 Vite 개발 서버를 외부에 따로
공개하지 않습니다.

## 4. 최초 1회 설치

JupyterHub에서 **팀 배포 계정**(`hwasungteam<N>`)으로 로그인하고 Terminal을 엽니다.
팀 공용 읽기·쓰기 작업 폴더는 `/home/jovyan/shared`입니다. 어느 팀원이 로그인해도 같은
파일을 보게 되므로 저장소와 배포 데이터는 이 폴더 아래에 둡니다.

```bash
cd /home/jovyan/shared
git clone <GITHUB_REPOSITORY_URL> safety-platform
cd safety-platform
bash deploy/jupyterhub/install.sh
```

설치 스크립트는 다음 작업을 수행합니다.

- `backend/.venv` 생성 및 Python 패키지 설치
- `npm ci`와 React production build
- sudo 없이 프로젝트의 `.tools/`에 `cloudflared` 설치
- 최초 실행일 때 JupyterHub용 `.env` 생성

필요 조건은 Git, Python 3.11 이상, Node.js/npm, curl입니다. 이 중 하나가 학교 이미지에 없다면
컨테이너 관리자에게 설치를 요청해야 합니다.

## 5. 환경 설정과 AI 모델

루트의 `.env`는 Git에 올라가지 않습니다. 기본값은 HTTPS 배포에 필요한
`COOKIE_SECURE=1`과 SQLite DB를 사용합니다.

```bash
nano .env
```

영상 분석을 사용하려면 모델을 팀 컨테이너에 한 번 준비합니다.

```bash
mkdir -p backend/weights
# 팀이 학습한 best.pt를 backend/weights/best.pt에 업로드
cd backend
.venv/bin/python -m scripts.download_fastreid_weights
cd ..
```

`best.pt`, `*.pth`, SQLite DB와 `.env`는 Git에서 제외되므로 `git pull`을 해도 유지됩니다.
GPU가 있으면 `REID_DEVICE=auto`가 자동으로 CUDA를 선택합니다. 로그인 후 브라우저 카메라를
설정하거나 녹화 영상을 업로드하면 분석이 시작됩니다.

위험 보고서의 LLM 설명을 켜려면 로컬 모델을 설치하는 대신 해커톤에서 제공하는 `openai`
SDK와 API 키를 사용합니다. 루트 `.env`에 다음 값을 입력하세요.

```dotenv
OPENAI_ENABLED=1
OPENAI_API_KEY=발급받은_API_키
OPENAI_MODEL=gpt-4o-mini
```

운영진이 별도 OpenAI-compatible gateway를 안내한 경우에만 `OPENAI_BASE_URL`도 설정합니다.
API 키는 저장소나 메신저에 올리지 마세요. 값 변경 후 서비스를 재시작합니다.

```bash
bash deploy/jupyterhub/service.sh restart
```

## 6. 외부 데모 시작
해커톤 시연에서는 위험 추세가 즉시 변하도록 같은 `.env`에 아래 설정을 사용합니다.


```dotenv
RISK_WINDOW_MODE=demo
RISK_REFRESH_SECONDS=5
RISK_SHORT_WINDOW_MINUTES=1
RISK_LONG_WINDOW_MINUTES=5
```

이 설정은 실제 저장된 사건의 최근 1분/5분 추세를 5초마다 갱신합니다. 운영 배포에서 원래
24시간/7일 구간을 사용하려면 `RISK_WINDOW_MODE=production`으로 변경하고 재시작하세요.

```bash
bash deploy/jupyterhub/service.sh start
```

기본 설정은 Cloudflare Quick Tunnel을 만들고 터미널에 임시
`https://....trycloudflare.com` 주소를 표시합니다. JupyterHub Terminal 탭을 닫아도
백그라운드 프로세스와 백엔드는 계속 실행됩니다. 팀 배포 컨테이너는 무활동으로 종료되지
않지만, 학교 운영진이 점검 또는 재생성을 수행한 경우에는 서비스를 다시 시작해야 합니다.

상태와 로그는 다음 명령으로 확인합니다.

```bash
bash deploy/jupyterhub/service.sh status
bash deploy/jupyterhub/service.sh logs
```

로그 화면은 `Ctrl+C`로 빠져나와도 실제 서비스는 종료되지 않습니다. 서비스를 종료하거나
재시작할 때만 다음 명령을 사용합니다.

```bash
bash deploy/jupyterhub/service.sh stop
bash deploy/jupyterhub/service.sh restart
```

## 7. 팀 배포 갱신

팀원은 각자의 개인 개발 계정에서 평소처럼 작업합니다.

```bash
git add <files>
git commit -m "변경 내용"
git push
```

배포할 때만 `hwasungteam<N>` Terminal에서 다음 한 줄을 실행합니다.

```bash
cd /home/jovyan/shared/safety-platform
bash deploy/jupyterhub/update.sh
```

이 명령은 `git pull --ff-only`, 의존성 동기화, React 재빌드, 서비스 재시작을 순서대로
수행합니다. 배포 계정에서 tracked 파일을 직접 수정한 경우에는 pull로 덮어쓰지 않고 중단합니다.
수정은 개인 계정에서 commit한 뒤 배포 계정이 pull하는 원칙을 지켜 주세요.

## 8. 고정 URL이 필요한 경우

Quick Tunnel 주소는 서비스를 재시작할 때마다 바뀝니다. 발표용 고정 도메인이 필요하면
Cloudflare Dashboard에서 named tunnel을 만들고 origin service를
`http://localhost:8000`으로 설정합니다. 받은 connector token과 공개 URL을 팀 배포 계정의
`.env`에만 저장합니다.

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=eyJ...
PUBLIC_URL=https://demo.example.com
```

토큰을 GitHub, 메신저, 문서 또는 개인 계정의 소스에 넣지 마세요. `.env` 변경 후 서비스를
재시작하면 named tunnel을 사용합니다.

## 9. GitHub 인증이 막힐 때

학교 네트워크에서 GitHub OAuth **인증 페이지만** 차단된다면 JupyterHub Terminal에서
GitHub CLI의 device login을 시작하고, 표시된 일회용 코드를 휴대폰 핫스팟에 연결한 개인
브라우저에서 승인할 수 있습니다. 승인이 끝나면 팀 배포 계정에 저장된 인증 정보를 이후
`git pull`에 사용합니다.

```bash
gh auth login
gh auth setup-git
gh auth status
```

팀 컨테이너에 `gh`가 없다면 개인 브라우저에서 fine-grained PAT를 발급한 뒤 Git credential
helper에 저장할 수 있지만, 토큰을 명령행·remote URL·셸 기록에 직접 넣지 마세요. 팀 배포
계정에 저장된 토큰은 그 계정을 공유하는 팀원이 사용할 수 있으므로 저장소 하나에 필요한 최소
권한만 부여하고 해커톤 종료 후 폐기합니다.

GitHub 인증 페이지만이 아니라 JupyterHub 컨테이너에서 `github.com` 또는 GitHub API 자체에
접속할 수 없다면, 핫스팟에서 한 번 인증하거나 토큰을 캐시해도 `git pull`은 되지 않습니다.
아래 명령으로 구분한 뒤 이 경우에는 학교 운영진에게 네트워크 허용을 요청해야 합니다.

```bash
curl -I https://github.com
git ls-remote origin
```

## 10. 문제 해결

| 증상 | 확인 및 해결 |
|---|---|
| `service.sh start`가 즉시 종료됨 | `service.sh logs`로 마지막 오류를 확인하고 `install.sh`를 다시 실행 |
| 외부 URL이 표시되지 않음 | 학교 outbound 방화벽이 Cloudflare Tunnel 연결을 막는지 확인; named tunnel이면 `.env`의 `PUBLIC_URL` 확인 |
| URL은 열리지만 로그인 유지 안 됨 | `.env`의 `COOKIE_SECURE=1` 확인 후 서비스 재시작 |
| 카메라 권한이 거부됨 | 반드시 cloudflared의 HTTPS URL로 접속하고 브라우저 사이트 권한 재설정 |
| AI 분석이 시작되지 않음 | 카메라 또는 녹화 영상 연결, 모델 경로, `nvidia-smi`, 서비스 로그 확인 |
| LLM 설명 대신 기본 보고서가 표시됨 | `OPENAI_ENABLED=1`, `OPENAI_API_KEY`, `OPENAI_MODEL`과 서비스 로그 확인 |
| `update.sh`가 변경 파일 때문에 중단됨 | 배포 계정에서 직접 수정하지 말고 개인 계정에서 commit/push; 필요한 배포 전용 값은 `.env`에만 저장 |
| `git pull` 인증 실패 | 위 GitHub 인증 절차를 사용하고 `gh auth status`로 캐시 상태 확인 |

## 11. 팀 보안 체크리스트

- [ ] `hwasungteam<N>` 비밀번호를 첫 로그인 직후 변경했는가?
- [ ] `.env`가 `.gitignore`에 있으며 `git status`에서 staged되지 않았는가?
- [ ] cloudflared 공개 페이지에 비밀번호, API 키, 개인정보가 노출되지 않는가?
- [ ] Cloudflare Tunnel token에 최소 권한을 사용하고 GitHub에 올리지 않았는가?
- [ ] 발표 전 `service.sh status`와 외부 URL에서 로그인·카메라·WebSocket을 확인했는가?
- [ ] 팀 배포 계정의 `backend/safety.db`, `.env`, 모델 파일을 안전하게 백업했는가?
- [ ] 모델 학습·실험은 개인 계정에서 하고 팀 계정에서는 배포 프로세스만 실행하는가?
- [ ] 데모 종료 후 `bash deploy/jupyterhub/service.sh stop`으로 cloudflared를 종료했는가?
- [ ] VS Code Code Tunnel을 별도로 사용했다면 발표 종료 후 `code tunnel unregister`로 등록을 해제했는가?
