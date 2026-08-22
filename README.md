# AI 안전관리 플랫폼

두 대의 USB 카메라와 BLE iBeacon 태그를 함께 사용해 작업자를 추적하고, 안전모 미착용·위험구역 접근·낙상 징후·폭염 노출을 분석하는 웹 데모입니다. 분석 기록을 기반으로 위험 추세를 계산하며, 업로드한 안전 문서를 OpenAI 임베딩으로 검색해 AI 보고서에 반영합니다.

현재 버전은 별도 회원가입이나 관리자 화면이 없는 익명 데모 방식입니다. 방문자가 **클라이언트 데모 시작**을 선택하면 격리된 임시 계정과 현장이 만들어지고, 사용자가 데모를 종료하거나 세션이 만료되면 해당 계정의 DB 기록과 저장소 파일을 함께 삭제합니다.

- Python 3.11 이상
- Node.js 20 이상과 npm
- Git
- AI 분석을 사용할 경우 사람·안전모·포즈 모델 파일
- 선택 사항: PostgreSQL/Supabase, OpenAI API 키, 기상청 API 키

- USB 카메라 2대 또는 녹화 영상 2개 동시 분석
- YOLO 기반 사람·안전모 검출
- BoT-SORT + FastReID 외형 특징 갤러리를 이용한 장기 ID 유지
- 평상시 2프레임 간격, 근접·교차·프레임 공백 시 매 프레임 적응형 추론
- 카메라 구도가 겹치거나 분리되어 있어도 카메라 간 전역 ID 학습
- Arduino Uno + HM-10 수신기 2대의 RSSI를 이용한 카메라 근접도 보정
- iBeacon 태그와 작업자 ID 연결 및 중복 ID 수동 병합
- 출입금지·추락위험·중장비·작업구역 폴리곤 설정
- 안전모 미착용, 위험구역 침입, 낙상·주저앉음·비틀거림 이벤트 분석
- 사건 단위 episode 집계와 위험 추세 보고서
- TXT, Markdown, PDF 지식문서 업로드 및 OpenAI Embeddings API 기반 RAG
- SQLite 기본 저장, PostgreSQL/Supabase 및 Supabase Storage 선택 지원
- DB는 UTC로 저장하고 화면/API에는 한국시간(KST)으로 표시

## 시스템 구성

```text
Chrome / Edge
├─ USB 카메라 A/B ─ WebSocket JPEG 업로드 ─┐
├─ Arduino BLE 수신기 A/B ─ Web Serial ───┤
└─ React 분석·설정 화면                    │
                                           ▼
FastAPI
├─ YOLO 사람/안전모/포즈 검출
├─ BoT-SORT + FastReID 단일 카메라 추적
├─ 카메라 간 전역 ID + BLE RSSI 보정
├─ 위험구역·행동·폭염 규칙
├─ Risk Engine + OpenAI RAG 보고서
└─ SQLite 또는 Supabase PostgreSQL/Storage
```

프로덕션 빌드에서는 FastAPI가 `frontend/dist`를 함께 제공하므로 화면, API, 쿠키, WebSocket이 같은 origin을 사용합니다.

## 필요 환경

- Python 3.11 이상
- Node.js 20 이상과 npm
- Git
- CUDA GPU 권장
  - 현재 requirements는 PyTorch CUDA 12.1 빌드를 사용합니다.
  - `REID_DEVICE=auto`이면 CUDA가 있을 때 FastReID가 GPU를 선택합니다.
- 카메라·BLE 사용 시 데스크톱 Chrome 또는 Edge
- 외부 장치 연결 시 HTTPS 또는 localhost

선택 하드웨어:

- USB 웹캠 1~2대
- Arduino Uno 1~2대
- HM-10 BLE 4.0 모듈 1~2대
- iBeacon 송신기 또는 iBeacon 앱을 실행하는 스마트폰 1~2대

## 빠른 시작

### Linux / Jupyter 환경

```bash
git clone <REPOSITORY_URL> safety-platform
cd safety-platform

cp .env.example .env
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -r backend/requirements.txt

cd frontend
npm ci
npm run build
cd ..

bash start.sh
```

접속 주소:

- Vite 개발 화면: `http://localhost:5173`
- FastAPI가 제공하는 빌드 화면: `http://localhost:8000`
- API 문서: `http://localhost:8000/docs`
- 상태 확인: `http://localhost:8000/health`

`start.sh`는 백엔드와 Vite 개발 서버를 함께 실행하고 `Ctrl+C`로 종료합니다.

### Windows PowerShell

```powershell
Copy-Item .env.example .env
py -3.11 -m venv backend\.venv
backend\.venv\Scripts\python -m pip install --upgrade pip
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt

Set-Location frontend
npm ci
npm run build
Set-Location ..

.\start.ps1
```

## 모델 준비

다음 경로에 모델 파일을 준비합니다.

```text
backend/weights/
├─ best.pt                 # 안전모/머리 검출 모델
├─ market_bot_R50.pth      # FastReID Market1501 BoT R50
└─ yolo11n-pose.pt         # 포즈 기반 행동 분석 모델
```

FastReID 가중치는 스크립트로 받을 수 있습니다.

```bash
cd backend
.venv/bin/python -m scripts.download_fastreid_weights
cd ..
```

`*.pt`, `*.pth`, `.env`, DB, 빌드 결과는 Git에서 제외됩니다. 모델과 비밀키를 커밋하지 마세요.

## 환경 설정

루트의 `.env.example`을 `.env`로 복사해 사용합니다. 변경한 환경변수는 백엔드 재시작 후 적용됩니다.

### 데모 세션

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `DEMO_IDLE_MINUTES` | `30` | 활동이 없는 데모 세션 만료 기준 |
| `DEMO_MAX_HOURS` | `2` | 데모 계정 최대 수명 |
| `DEMO_CLEANUP_INTERVAL_SECONDS` | `60` | 만료 계정 정리 주기 |
| `DEMO_MAX_ACTIVE_SESSIONS` | `5` | 동시 활성 데모 사용자 수 |
| `COOKIE_SECURE` | `0` | 로컬 HTTP는 `0`, HTTPS 배포는 `1` |

각 데모 사용자는 별도 계정과 현장을 사용합니다. 데모 종료 또는 만료 시 다음 데이터가 정리됩니다.

- 사용자, 세션, 현장, 위험구역
- 이벤트와 사건 episode
- 위험 예측·AI 보고서
- 지식문서와 임베딩 청크
- Supabase 또는 로컬 저장소의 문서·스냅샷·클립

저장소 파일 삭제에 실패하면 계정을 먼저 차단하고 다음 정리 주기에 다시 시도합니다.

### 영상 분석과 ReID

| 변수 | 기본값 | 설명 |
|---|---:|---|
| `MODEL_CONFIDENCE` | `0.4` | YOLO 검출 신뢰도 |
| `MODEL_IMAGE_SIZE` | `384` | YOLO 입력 크기 |
| `LIVE_INFER_EVERY` | `2` | 평상시 검출 간격; 근접·교차 시 자동으로 매 프레임 처리 |
| `TRACK_BUFFER_FRAMES` | `90` | BoT-SORT 트랙 유지 버퍼 |
| `REID_MATCH_THRESHOLD` | `0.72` | 장기 ReID 외형 매칭 기준 |
| `REID_GALLERY_SECONDS` | `120` | 일반 외형 특징 보존 시간 |
| `REID_SWITCH_MARGIN` | `0.10` | ID 변경에 필요한 특징 점수 차이 |
| `REID_SWITCH_CONFIRM_FRAMES` | `3` | 애매한 ID 변경을 확인할 깨끗한 프레임 수 |
| `REID_FRAME_GAP_SECONDS` | `0.45` | 이 간격 이상이면 위치보다 외형을 우선하는 기준 |
| `REID_DEVICE` | `auto` | `auto`, `cpu`, `cuda`, `cuda:0` 등 |

추적 정책은 다음과 같습니다.

- 평상시 매칭은 외형 특징 65%, 움직임 25% 중심입니다.
- 사람이 가까워지거나 겹치고, 입력 공백이 생기면 외형 비중을 약 86%로 높입니다.
- 겹치는 동안에는 잘못 잘린 특징과 이동 이력을 학습하지 않습니다.
- 특징 차이가 애매하면 기존 ID를 유지하고, 깨끗한 프레임에서 연속 확인한 뒤 변경합니다.
- 활성 BLE 태그와 연결된 ID는 일반 ReID 갤러리 시간이 지나도 복원 후보로 유지합니다.

## 사용 순서

1. 첫 화면에서 **클라이언트 데모 시작**을 선택합니다.
2. 기본으로 생성된 데모 현장을 사용하거나 새 현장을 추가합니다.
3. 분석 화면에서 카메라 A/B를 검색해 서로 다른 USB 장치를 선택합니다.
4. 카메라를 시작하거나 녹화 영상을 업로드합니다.
5. 필요한 경우 출입금지·추락위험·중장비·작업구역을 그립니다.
6. BLE를 사용한다면 수신기 A/B를 연결하고 태그를 작업자 ID에 등록합니다.
7. 이벤트와 위험 추세를 확인하고 필요하면 지식문서를 등록해 AI 보고서를 생성합니다.
8. 시연이 끝나면 **데모 종료**를 눌러 임시 데이터를 즉시 삭제합니다.

페이지에 들어온 직후에는 영상을 자동 분석하지 않습니다. 카메라나 녹화 영상을 설정하기 전까지 입력 요청 메시지가 표시됩니다.

## 카메라 2대와 전역 ID

- 카메라 A와 B는 동일 제품이어도 브라우저의 `deviceId`로 구분합니다.
- 두 카메라는 겹치는 구도와 분리된 구도를 모두 사용할 수 있습니다.
- 시스템은 동시 관측과 카메라 간 이동 기록으로 배치 형태를 학습합니다.
- 배치를 바꿨다면 분석 화면의 **배치 다시 학습**을 누릅니다.
- 처음에는 한 사람씩 화면을 지나가게 하면 전역 ID와 BLE 연결을 안정적으로 초기화할 수 있습니다.
- USB 대역폭이 부족하면 카메라를 서로 다른 USB 컨트롤러에 연결하세요.

브라우저는 카메라별 320×240, 최대 10 FPS로 최신 프레임만 전송합니다. 서버가 오래된 프레임을 쌓지 않아 지연이 계속 누적되는 것을 방지합니다.

## BLE / HM-10 설정

BLE는 픽셀 좌표를 직접 측정하지 않습니다. 태그와 연결된 작업자의 장기 ID를 보호하고, 수신기 A/B의 RSSI 차이로 어느 카메라에 가까운지 보정합니다.

Arduino 스케치는 분석 화면의 **Arduino 코드 받기** 또는 [frontend/public/hm10_ibeacon_scanner.ino](frontend/public/hm10_ibeacon_scanner.ino)에서 받을 수 있습니다.

배선:

```text
HM-10 TXD -> Arduino D4
Arduino D5 -> 전압 분배기 -> HM-10 RXD
GND       -> GND
VCC       -> 모듈 사양에 맞는 전원
Serial    -> 9600 baud
```

스케치는 시작할 때 `AT`, `AT+IMME1`, `AT+ROLE1`, `AT+RESET`을 실행하고 `AT+DISI?`를 반복 호출해 iBeacon을 스캔합니다.

연결 절차:

1. 수신기 A를 카메라 A 옆, 수신기 B를 카메라 B 옆에 고정합니다.
2. Arduino에 스케치를 업로드한 뒤 Serial Monitor를 닫습니다.
3. HTTPS 페이지를 데스크톱 Chrome/Edge로 엽니다.
4. BLE 수신기 A/B의 **USB 연결**을 각각 눌러 올바른 Arduino 포트를 선택합니다.
5. 태그는 같은 UUID를 사용해도 되지만 Major/Minor 조합은 사람마다 다르게 설정합니다.
6. 최초 등록은 한 사람씩 카메라 앞에 세우고 표시된 태그를 해당 `person-xxxxxx` ID에 연결합니다.

Web Serial은 Safari와 iPhone 브라우저에서 지원되지 않습니다. iPhone은 iBeacon 송신 태그로 사용할 수 있지만 Arduino USB 수신기 연결은 데스크톱 Chrome/Edge에서 해야 합니다.

## OpenAI RAG와 AI 보고서

지식문서 등록과 검색에는 OpenAI Embeddings API를 사용합니다. 기본 모델은 `text-embedding-3-small`, 벡터 크기는 1024입니다.

```dotenv
OPENAI_API_KEY=발급받은_API_KEY
EMBEDDING_MODEL_NAME=text-embedding-3-small
EMBEDDING_DIM=1024

OPENAI_ENABLED=1
OPENAI_MODEL=gpt-4o-mini
```

- 허용 문서: `.txt`, `.md`, `.pdf`
- 최대 크기: 10 MB
- 문서는 현장별로 분리되어 검색됩니다.
- AI 보고서는 Risk Engine의 점수와 위험등급을 변경하지 못하며 설명과 권고만 생성합니다.
- 검색 결과가 약하거나 OpenAI 호출이 실패하면 근거 부족을 표시한 기본 보고서를 사용합니다.
- API 키 없이 지식문서를 등록하면 임베딩 생성에 실패합니다.

API 키와 Supabase service-role key는 반드시 백엔드 `.env`에만 두고 프론트엔드 변수나 Git에 넣지 마세요.

## 데이터베이스와 저장소

### 기본 로컬 구성

```dotenv
DATABASE_URL=sqlite:///./safety.db
LOCAL_STORAGE_ROOT=storage
```

일반 실행 스크립트는 백엔드 디렉터리에서 서버를 시작하므로 SQLite 파일은 보통 `backend/safety.db`, 업로드 파일은 `backend/storage/`에 생성됩니다.

### Supabase 구성

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres
SUPABASE_URL=https://[PROJECT].supabase.co
SUPABASE_SERVICE_ROLE_KEY=[SERVICE_ROLE_KEY]
SUPABASE_DOCUMENT_BUCKET=safety-documents
SUPABASE_SNAPSHOT_BUCKET=event-snapshots
SUPABASE_CLIP_BUCKET=event-clips
```

세 bucket은 private으로 생성하는 것을 권장합니다. `SUPABASE_URL`과 service-role key를 모두 설정하면 Supabase Storage를 사용하고, 둘 다 없으면 로컬 저장소를 사용합니다. PostgreSQL에서는 pgvector를 사용하며 서버 시작 시 필요한 테이블·인덱스·시간 컬럼 마이그레이션을 확인합니다.

## JupyterHub 외부 배포

최초 설치:

```bash
bash deploy/jupyterhub/install.sh
```

서비스 관리:

```bash
bash deploy/jupyterhub/service.sh start
bash deploy/jupyterhub/service.sh status
bash deploy/jupyterhub/service.sh logs
bash deploy/jupyterhub/service.sh restart
bash deploy/jupyterhub/service.sh stop
```

GitHub 최신 버전 배포:

```bash
bash deploy/jupyterhub/update.sh
```

Quick Tunnel URL은 재시작하면 바뀝니다. 고정 URL은 Cloudflare named tunnel의 token과 `PUBLIC_URL`을 `.env`에 설정하세요. 자세한 절차는 [docs/deployment-jupyterhub.md](docs/deployment-jupyterhub.md)를 참고하세요.

## 테스트와 빌드

백엔드 전체 테스트:

```bash
cd backend
.venv/bin/python -m unittest discover -s tests
```

프론트엔드 검사와 빌드:

```bash
cd frontend
npm run lint
npm run build
```

오프라인 영상 분석:

```bash
cd backend
.venv/bin/python -m scripts.analyze_video2 /path/to/video.mp4
```

평가 데이터셋과 지표 산출 절차는 [evaluation/README.md](evaluation/README.md)를 참고하세요.

## 문제 해결

| 증상 | 확인할 내용 |
|---|---|
| 카메라를 찾을 수 없음 | HTTPS/localhost인지, 브라우저 권한이 허용됐는지, 다른 앱이 카메라를 사용 중인지 확인 |
| 같은 제품 카메라 두 대 중 하나만 열림 | 서로 다른 `deviceId`를 선택하고 가능하면 다른 USB 컨트롤러에 연결 |
| 분석 박스가 느리거나 잔상이 보임 | 화면의 전송/처리 FPS, `MODEL_IMAGE_SIZE`, `LIVE_INFER_EVERY`, GPU 사용률 확인 |
| 교차할 때 ID가 바뀜 | 전신이 보이게 배치하고 FastReID 가중치·CUDA 로드 확인, BLE 태그를 초기 ID에 연결 |
| BLE USB 버튼이 동작하지 않음 | 데스크톱 Chrome/Edge와 HTTPS 사용, Serial Monitor 종료 후 포트 재선택 |
| 태그가 나타나지 않음 | HM-10 central 설정, iBeacon 송신 상태, UUID/Major/Minor, 9600 baud 확인 |
| 지식문서 업로드 실패 | `OPENAI_API_KEY`, 임베딩 차원, Supabase bucket 또는 로컬 저장소 권한 확인 |
| AI 보고서에 문서가 반영되지 않음 | 문서 chunk 수, RAG 유사도 임계값, 서버의 `RAG retrieval` 로그 확인 |
| 외부 URL 접속 실패 | `service.sh status`, `service.sh logs`, cloudflared 상태 확인 |

## 보안과 운영 주의사항

- `.env`, API 키, Supabase service-role key, Cloudflare tunnel token을 커밋하지 마세요.
- 공개 데모에는 실제 개인정보나 민감한 현장 영상을 올리지 마세요.
- 데모 종료 버튼을 누르면 해당 임시 사용자의 기록과 파일이 삭제됩니다.
- 서버가 비정상 종료되어도 다음 시작 시 만료 데모 계정 정리를 다시 수행합니다.
- Quick Tunnel은 접근 인증을 제공하지 않으므로 필요하면 Cloudflare Access나 별도 접근제어를 추가하세요.
- 이 시스템의 위험 점수는 관측된 위험행동 추세이며 실제 사고 확률이나 법적 안전판정을 의미하지 않습니다.

## 저장소 구조

```text
backend/                 FastAPI, 분석 파이프라인, DB, RAG, 테스트
frontend/                React/Vite 분석·설정 화면
frontend/public/         Arduino 스케치와 정적 자산
deploy/jupyterhub/       설치·서비스·업데이트 스크립트
docs/                    배포 문서
evaluation/              영상 평가 데이터셋·도구·보고서
start.sh / start.ps1     로컬 개발 실행 스크립트
```
