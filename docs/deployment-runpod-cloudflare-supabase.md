# 해커톤 GPU 배포: Cloudflare + RunPod + Supabase

이 문서는 현재 FastAPI API, 쿠키 인증, WebSocket 카메라 업로드, YOLO 안전모/사람 감지,
FastReID 다중 카메라 ID 연결을 유지하는 배포 절차입니다.

## 최종 구조

```text
https://app.example.com
  -> Cloudflare Pages (frontend/)

https://api.example.com
  -> Cloudflare DNS + named Tunnel
  -> RunPod GPU Pod (Dockerfile, FastAPI, cloudflared)
  -> Supabase PostgreSQL
```

프론트와 API는 반드시 같은 루트 도메인의 HTTPS 서브도메인으로 구성하는 것을 권장합니다.
현재 인증 쿠키가 `SameSite=Lax`이므로 `app.example.com`과 `api.example.com` 조합이 가장
안전합니다.

## 0. 준비물

- GitHub에 푸시된 배포 브랜치
- Cloudflare에 연결된 도메인
- Docker Hub 계정과 로컬 Docker Desktop
- Supabase 계정
- RunPod 계정과 약 $20 크레딧
- 로컬의 Git 제외 모델 파일

필수 모델 파일:

```text
backend/weights/best.pt
backend/weights/market_bot_R50.pth
backend/yolov8n.pt
```

현재 `Dockerfile`은 모델, `.env`, SQLite DB, 영상이 이미지에 섞이지 않도록
`.dockerignore`로 제외합니다. 모델은 RunPod의 `/workspace/models` 볼륨으로 공급합니다.

## 1. 배포 브랜치 준비

현재 작업을 커밋한 뒤 배포 브랜치에 푸시합니다.

```powershell
git status
git add Dockerfile .dockerignore backend deploy docs
git commit -m "chore: prepare GPU production deployment"
git push origin feature/track-people
```

장기적으로는 검증 후 `main`에 병합하고 Pages와 이미지 빌드가 `main`을 사용하게 만드는 것이
좋습니다. 당장 시연할 때는 두 서비스 모두 동일한 `feature/track-people` 커밋을 사용합니다.

## 2. Supabase PostgreSQL 생성

1. Supabase Dashboard에서 `New project`를 선택합니다.
2. 강력한 Database password를 생성하고 별도로 보관합니다.
3. RunPod GPU를 구할 수 있는 지역과 가능한 한 가까운 리전을 선택합니다.
4. 프로젝트 생성 후 상단의 `Connect`를 선택합니다.
5. `Session pooler`, 포트 `5432` 연결 문자열을 복사합니다.

형식:

```text
postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
```

RunPod처럼 IPv4 연결이 필요할 수 있는 지속 실행 서버와 Pod 교체에 대응하기 쉬우므로 Direct
URL 대신 Session pooler를 사용합니다. 비밀번호의 `@`, `/`, `#`, `:` 등은 URL 인코딩해야
합니다.

앱은 첫 시작 시 SQLAlchemy metadata로 빈 Supabase DB에 테이블을 자동 생성합니다. 기존
`backend/safety.db` 데이터는 자동 이전되지 않습니다. 해커톤 신규 데이터라면 새로 회원가입하면
되고, 기존 데이터가 필요하면 별도 마이그레이션이 필요합니다.

## 3. Cloudflare named Tunnel 생성

1. Cloudflare Dashboard에서 `Networking > Tunnels`로 이동합니다.
2. `Create a tunnel`을 선택하고 이름을 `safety-platform-api`로 지정합니다.
3. Connector 환경은 Docker를 선택합니다.
4. 화면의 실행 명령에서 `--token` 뒤의 긴 토큰을 복사합니다.
5. Tunnel의 `Routes` 또는 `Public Hostnames`에서 Published application을 추가합니다.

```text
Hostname: api.example.com
Service type: HTTP
Service URL: localhost:8000
```

Tunnel이 DNS CNAME을 자동 생성합니다. 토큰은 GitHub, Docker image, `.env`에 넣지 않습니다.
나중에 RunPod Secret으로 등록합니다.

## 4. GPU Docker 이미지 빌드 및 푸시

저장소 루트에서 실행합니다.

```powershell
docker login
docker build -t DOCKERHUB_USER/safety-platform:gpu .
docker push DOCKERHUB_USER/safety-platform:gpu
```

빌드 이미지에는 CUDA PyTorch, 맞는 torchvision, FastAPI 의존성, Noto CJK 폰트와
`cloudflared`가 포함됩니다. 모델과 비밀값은 포함되지 않습니다.

로컬 GPU 없이도 이미지를 빌드할 수 있지만 이미지 크기가 크고 최초 빌드/푸시에 시간이
걸립니다. 발표 전날까지 Docker Hub push를 끝냅니다. 비공개 Docker Hub repository를 쓰면
RunPod Template에 registry credential도 설정해야 합니다.

## 5. RunPod Network Volume과 모델 업로드

### 5.1 볼륨 생성

1. RunPod `Storage`에서 약 20GB Network Volume을 생성합니다.
2. 사용할 GPU가 존재하는 Secure Cloud 데이터센터를 고릅니다.
3. Network Volume은 Pod 생성 후 붙일 수 없으므로 반드시 Pod 배포 시 선택합니다.

### 5.2 임시 PyTorch Pod로 파일 수신

Network Volume에 파일을 처음 넣기 위해 공식 RunPod PyTorch Pod를 임시로 하나 배포하고
해당 Network Volume을 `/workspace`에 연결합니다. Pod의 Web Terminal에서 실행합니다.

```bash
mkdir -p /workspace/models
cd /workspace/models
```

로컬 Windows에는 RunPod CLI를 설치합니다. 설치 후 각 파일에 대해 실행합니다.

```powershell
runpodctl send backend/weights/best.pt
runpodctl send backend/weights/market_bot_R50.pth
runpodctl send backend/yolov8n.pt
```

각 명령은 일회용 receive 코드를 출력합니다. Pod Web Terminal에서 해당 코드를 사용합니다.

```bash
cd /workspace/models
runpodctl receive RECEIVE_CODE_FOR_BEST
runpodctl receive RECEIVE_CODE_FOR_FASTREID
runpodctl receive RECEIVE_CODE_FOR_YOLO
ls -lh /workspace/models
```

예상 파일 크기:

```text
best.pt                 약 6 MB
yolov8n.pt              약 7 MB
market_bot_R50.pth      약 301 MB
```

파일이 확인되면 임시 PyTorch Pod는 `Terminate`합니다. Network Volume은 삭제하지 않습니다.

## 6. RunPod Secret 생성

RunPod `Settings` 또는 `Secrets`에서 다음 두 Secret을 만듭니다.

```text
이름: cf_tunnel_token
값: Cloudflare named tunnel token

이름: supabase_database_url
값: Supabase Session pooler URL
```

실제 Secret 값은 로그나 캡처 화면에 노출하지 않습니다.

## 7. RunPod 애플리케이션 Pod 배포

1. `Pods > Deploy`에서 `RTX A5000 24GB`를 우선 선택합니다.
2. 없으면 L4, RTX 3090, RTX 4090 순서로 선택합니다.
3. 생성한 20GB Network Volume을 연결합니다.
4. `Edit Template`에서 다음을 지정합니다.

```text
Container image: DOCKERHUB_USER/safety-platform:gpu
Container disk: 20GB 이상
Network volume: 앞에서 만든 볼륨
```

Cloudflare Tunnel을 사용하므로 HTTP/TCP public port는 필수가 아닙니다. 초기 디버깅이 필요하면
`Expose HTTP Ports`에 `8000`을 임시 추가할 수 있습니다.

Environment Variables:

```dotenv
ANALYSIS_ENABLED=1
PORT=8000
REID_DEVICE=cuda
REID_BACKEND=fastreid

CORS_ORIGINS=https://app.example.com
COOKIE_SECURE=1
SESSION_COOKIE_NAME=safety_session
SESSION_DAYS=7

DATABASE_URL={{ RUNPOD_SECRET_supabase_database_url }}
CLOUDFLARE_TUNNEL_TOKEN={{ RUNPOD_SECRET_cf_tunnel_token }}

HELMET_MODEL_PATH=/workspace/models/best.pt
PERSON_MODEL_PATH=/workspace/models/yolov8n.pt
FASTREID_WEIGHTS_PATH=/workspace/models/market_bot_R50.pth
FONT_PATH=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
```

`deploy/runpod.env.example`에도 같은 비밀값 제외 설정이 있습니다.

Pod를 배포하면 시작 스크립트가 다음을 검증합니다.

1. 모델 3개 존재 여부
2. CUDA 사용 가능 여부
3. Supabase 연결 및 테이블 생성
4. Uvicorn 단일 worker 시작
5. Cloudflare Tunnel connector 시작

현재 분석 상태와 사람 ID manager는 프로세스 메모리에 있기 때문에 Uvicorn은 반드시
`--workers 1`이어야 합니다. 제공된 시작 스크립트가 이를 강제합니다.

## 8. 백엔드 확인

Cloudflare Tunnel이 `Healthy`가 된 뒤 확인합니다.

```powershell
Invoke-RestMethod https://api.example.com/health
```

정상 응답:

```json
{"status":"ok"}
```

RunPod 로그에서 다음도 확인합니다.

```text
[startup] CUDA available: True
[startup] FastAPI and Cloudflare Tunnel started.
```

관리자 계정이 필요하면 일반 회원가입 후 RunPod Web Terminal에서 실행합니다.

```bash
cd /app/backend
python -m scripts.create_admin
```

## 9. Cloudflare Pages 배포

1. `Workers & Pages > Create application > Pages`를 선택합니다.
2. GitHub 저장소를 연결합니다.
3. 다음 빌드 설정을 입력합니다.

```text
Production branch: feature/track-people (또는 병합한 main)
Framework preset: React (Vite)
Root directory: frontend
Build command: npm run build
Build output directory: dist
```

Production 환경변수:

```dotenv
VITE_API_BASE=https://api.example.com
VITE_WS_BASE=wss://api.example.com/ws
NODE_VERSION=22.16.0
```

`VITE_` 변수에는 비밀값을 넣지 않습니다. 저장 후 첫 배포를 실행합니다.

## 10. Pages 도메인과 Cloudflare DNS

Pages 프로젝트의 `Custom domains > Set up a domain`에서 다음을 추가합니다.

```text
app.example.com
```

Pages 화면을 통해 추가하면 Cloudflare DNS 레코드와 인증서가 자동 구성됩니다. 최종 구조는
다음과 비슷합니다.

```text
app.example.com -> Cloudflare Pages
api.example.com -> Cloudflare Tunnel
```

두 주소 모두 HTTPS가 활성화된 후 RunPod 환경변수의 `CORS_ORIGINS`가 정확히
`https://app.example.com`인지 확인합니다. 뒤에 `/`를 붙이지 않습니다.

## 11. 전체 시연 점검

1. `https://api.example.com/health`가 200인지 확인합니다.
2. `https://app.example.com`에서 회원가입과 로그인을 확인합니다.
3. 브라우저 개발자 도구에서 로그인 응답의 Secure/HttpOnly 쿠키를 확인합니다.
4. Network > WS에서 `/ws`가 `101 Switching Protocols`인지 확인합니다.
5. 브라우저 카메라를 등록하고 `/ws/camera-upload/{camera_id}`가 연결되는지 확인합니다.
6. 카메라별 입구/출구 ROI를 만들고 Global ID 인계가 일어나는지 확인합니다.
7. RunPod 로그에서 CUDA OOM과 모델 누락 오류가 없는지 확인합니다.
8. Supabase Table Editor에서 users, sites, events 데이터가 생성되는지 확인합니다.

## 12. 발표 당일 운영

- 발표 최소 30분 전에 Pod를 배포해 이미지 pull과 모델 로딩을 끝냅니다.
- GPU Pod의 브라우저 카메라 권한과 행사장 네트워크를 미리 확인합니다.
- 동시 카메라가 많으면 추론은 detector lock에 의해 순차 처리되므로 두 대부터 검증합니다.
- 실패에 대비해 분석 완료 화면 녹화본도 준비합니다.

발표가 끝나면 GPU Pod를 `Terminate`합니다. Network Volume은 유지되므로 다음에 같은 볼륨을
붙여 새 Pod를 배포할 수 있습니다. Network Volume을 붙인 Pod는 일반 Stop 대신 Terminate가
필요할 수 있습니다. 볼륨도 더 이상 필요 없으면 모델을 백업한 뒤 별도로 삭제합니다.

## 13. 예상 비용

2026-08-07 공개 가격의 RTX A5000 시작가인 시간당 $0.27을 기준으로 계산하면:

```text
12시간: 약 $3.24
36시간: 약 $9.72
72시간: 약 $19.44
20GB Network Volume: 월 환산 약 $1.40
```

Cloudflare Pages/DNS/Tunnel과 Supabase는 해커톤 사용량에서 무료 한도 내 사용을 목표로
합니다. 20GB 표준 Network Volume은 $0.07/GB/월 기준으로 약 $1.40/월입니다. GPU 가격과
재고는 데이터센터·Cloud 유형에 따라 달라질 수 있으므로 배포 직전 RunPod 콘솔의 최종 금액을
확인하세요. 기존 도메인이 없다면 도메인 등록비도 별도입니다. RunPod에 $20 정도만 충전하고
테스트/발표 시간에만 Pod를 실행하는 것이 적절합니다.

## 공식 문서

- [Cloudflare Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/)
- [Cloudflare Tunnel published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)
- [Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [RunPod environment variables and secrets](https://docs.runpod.io/pods/templates/environment-variables)
- [RunPod file transfer](https://docs.runpod.io/pods/storage/transfer-files)
- [RunPod network volumes](https://docs.runpod.io/storage/network-volumes)
- [RunPod Pod pricing](https://docs.runpod.io/pods/pricing)
