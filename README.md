# AI 현장 안전 · 다중 카메라 사람 추적 플랫폼

두 대의 현장 카메라 또는 두 개의 테스트 영상에서 사람을 검출·추적하고, 카메라가 바뀌어도 동일 인물의 `global_person_id`를 유지하는 FastAPI + React 프로토타입입니다. 안전모와 위험구역도 함께 판정합니다.

## 주요 기능

- YOLO 기반 사람·안전모 검출
- 카메라별 `local_track_id` 생성 및 프레임 간 추적
- 사람 crop의 외형 특징 벡터와 객체 이미지 품질 계산
- 카메라별 입구·출구 ROI 편집
- 외형 유사도, 카메라 간 이동 시간, ROI, 이동 방향, 객체 품질을 결합한 카메라 간 Re-ID
- 동일 인물 매칭 시 기존 `global_person_id` 승계
- 출입금지·추락위험·중장비 작업반경 구역 판정 및 이벤트 기록
- 카메라 2대, 테스트 영상 2개 또는 혼합 입력 지원

## 실행

필요 조건은 Python 3.11+, Node.js 20+입니다.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m scripts.download_fastreid_weights

cd ..\frontend
npm install
cd ..
.\start.ps1
```

기본 분석 영상까지 자동 실행하려면 다음을 사용합니다.

```powershell
.\start.ps1 -WithAnalyzer
```

Linux/macOS에서는 `./start.sh` 또는 `./start.sh --with-analyzer`를 사용합니다. 프론트엔드는 기본적으로 `http://localhost:5173`, API 문서는 `http://localhost:8000/docs`에서 열립니다.

학교 Linux JupyterHub 팀 컨테이너가 유일한 배포 환경입니다. 최초 설치와 외부 HTTPS 공개는
[JupyterHub 배포 가이드](docs/deployment-jupyterhub.md)를 따르세요.

```bash
bash deploy/jupyterhub/install.sh
bash deploy/jupyterhub/service.sh start
```

## OpenAI 위험 보고서 설정

위험 예측 보고서는 해커톤 제공 SDK 중 공식 `openai` Python SDK를 사용합니다. 로컬 LLM
서버는 필요하지 않습니다. 루트 `.env`에 발급받은 API 키와 사용할 모델을 설정하세요.

```dotenv
OPENAI_ENABLED=1
OPENAI_API_KEY=발급받은_API_키
OPENAI_MODEL=gpt-4o-mini
```

해커톤에서 별도 OpenAI-compatible gateway를 제공한 경우에만 다음 값도 추가합니다.

```dotenv
OPENAI_BASE_URL=https://제공받은-gateway.example/v1
```

설정 후 백엔드를 재시작하면 `/health` 응답의 `llm_provider`가 `openai`로 표시됩니다.
API 키가 없거나 OpenAI 호출이 실패하면 위험 등급 계산은 중단되지 않고, 기존 Risk Engine
결과로 만든 수치 기반 보고서가 반환됩니다. API 키는 `.env`에만 저장하고 Git에 커밋하지
마세요.

`REID_DEVICE=auto`는 CUDA가 있으면 GPU를 사용하고, 없으면 CPU를 사용합니다.

### 해커톤 데모용 위험 시간축

데모에서는 실제 저장된 사건을 기준으로 최근 `1분`과 `5분` 추세를 보여 주며, 현황 화면이
5초마다 자동 갱신됩니다. API 호환성을 위해 요청 값은 기존 `24h`, `7d`를 유지하고 화면
라벨과 계산 구간만 다음 설정으로 매핑합니다.

```dotenv
RISK_WINDOW_MODE=demo
RISK_REFRESH_SECONDS=5
RISK_SHORT_WINDOW_MINUTES=1
RISK_LONG_WINDOW_MINUTES=5
```

운영용 시간축으로 되돌릴 때는 다음처럼 바꾸고 백엔드를 재시작합니다.

```dotenv
RISK_WINDOW_MODE=production
```

AI 보고서는 버튼을 눌렀을 때만 생성되므로 자동 갱신 중 OpenAI API가 반복 호출되지 않습니다.

## 카메라 간 ID 연결 사용법

1. 관제 화면에서 카메라를 검색하거나 각 슬롯에 테스트 영상을 선택합니다.
2. 두 입력을 모두 시작합니다.
3. 각 카메라의 관제 화면을 선택하고 구역 편집기에서 `카메라 입구 ROI`와 `카메라 출구 ROI`를 그립니다.
4. 카메라 1의 트랙이 출구 ROI에 들어오면 즉시 전환 후보가 됩니다.
5. 제한 시간 안에 카메라 2의 입구 ROI에 나타난 새 트랙은 복합 점수로 비교됩니다.
6. 임계값을 넘으면 카메라 1의 `global_person_id`를 그대로 사용합니다. 화면의 작업자 카드에서 이전 카메라, Re-ID 유사도와 이동 시간을 확인할 수 있습니다.

카메라 간 실제 연결 관계가 맞도록 각 영상에 입구·출구 ROI를 지정해야 합니다. ROI가 없으면 새 카메라에서 새 `global_person_id`가 발급됩니다.

## 매칭 점수

현재 총점은 다음 신호를 결합합니다.

| 신호 | 가중치 |
|---|---:|
| 외형 특징 cosine 유사도 | 65% |
| 카메라 간 이동 시간 | 15% |
| 출구·입구 이동 방향 | 12% |
| 객체 이미지 품질 | 8% |

기본값은 외형 유사도 `0.60` 이상, 총점 `0.68` 이상, 최대 이동 시간 `30초`입니다. 입구에서 먼저 검출된 경우에도 30프레임 동안 종료 후보를 다시 비교합니다. `.env`에서 다음 값을 조정할 수 있습니다.

```dotenv
TRACK_MAX_MISSED_FRAMES=12
REID_MAX_TRANSITION_SECONDS=30
REID_MIN_SIMILARITY=0.60
REID_SCORE_THRESHOLD=0.68
REID_DEEP_WEIGHT=0.85
REID_IMAGE_SIZE=192
REID_ENTRY_GRACE_FRAMES=30
REID_ROI_MARGIN=0.025
REID_BACKEND=fastreid
REID_DEVICE=auto
FASTREID_WEIGHTS_PATH=backend/weights/market_bot_R50.pth
```

현재 외형 특징은 공식 FastReID Market1501 BoT ResNet-50의 2048차원 임베딩과 spatial HSV/Lab descriptor를 결합합니다. 가중치가 없거나 로드에 실패하면 YOLO 특징으로 폴백하며, 관제 영상 하단에서 `FastReID` 또는 `Fallback Re-ID` 상태를 확인할 수 있습니다.

## 구조

```text
backend/app/services/
├─ detector.py          # 사람·안전모 검출
├─ person_tracking.py   # 로컬 추적, 외형 특징, 글로벌 ID 전환
├─ pipeline.py          # 추적·Re-ID·안전 판정 통합
├─ safety_rules.py      # 위험구역 판정 및 ROI 유형
└─ analysis_service.py  # 카메라별 분석 서비스와 현장 공유 ID 매니저

frontend/src/
├─ BrowserCameraController.jsx # 카메라/테스트 영상 2개 입력
├─ ZoneEditor.jsx              # 위험구역 및 입·출구 ROI 편집
└─ App.jsx                     # 관제 지표와 global/local ID 표시
```

상세 설계는 [docs/project-overview.md](docs/project-overview.md)를 참고하세요.
