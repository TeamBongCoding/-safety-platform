# 영상 평가 데이터셋

이 폴더는 안전 플랫폼의 안전모·위험구역·행동·폭염·사람 추적 성능을 같은 기준으로 반복 평가하기 위한 공간입니다.

## 폴더 구조

```text
evaluation/
├─ annotations/
│  ├─ clip_annotations.csv       # 40개 영상의 정답표
│  └─ SCHEMA.md                   # CSV 열과 채점 기준
├─ configs/
│  ├─ README.md                   # 구역/폭염 설정 방법
│  └─ heat_fixtures.json          # 폭염 상태를 재현하기 위한 시간 구간
├─ scripts/
│  └─ validate_dataset.py         # CSV 및 영상 파일 사전 검사
├─ videos/
│  └─ .gitkeep                    # 촬영한 MP4 40개를 여기에 저장
├─ results/
│  └─ .gitkeep                    # 자동 평가 원시 결과 저장 위치
├─ reports/
│  └─ .gitkeep                    # 요약 성능 보고서 저장 위치
└─ VIDEO_RECORDING_GUIDE.md       # 공통 규칙과 40개 촬영 지시서
```

`videos/`와 `results/`의 생성 파일은 Git에 포함되지 않습니다. 원본 영상은 로컬 또는 별도 공유 저장소에 보관하세요.

## 촬영 전 체크

1. `VIDEO_RECORDING_GUIDE.md`의 안전수칙과 공통 촬영 조건을 먼저 읽습니다.
2. 각 영상은 표에 적힌 파일명 그대로 `evaluation/videos/`에 저장합니다.
3. 가로 영상, 30 FPS, 고정 카메라를 사용하고 특별한 지시가 없으면 전신과 발이 보이게 합니다.
4. 작업구역과 출입금지구역 영상은 바닥 경계를 밝은 테이프로 표시합니다.
5. 쓰러짐 동작은 실제 낙상이 아니라 두꺼운 매트 위에서 낮은 자세부터 안전하게 연출합니다.
6. 폭염 영상은 실제 고온 노출이 필요하지 않습니다. 평가 프로그램이 폭염 상태를 강제로 재현합니다.

## 현재 가능한 검사

프로젝트 루트에서 다음 명령을 실행합니다.

```powershell
python evaluation/scripts/validate_dataset.py
```

영상 40개가 모두 준비된 뒤에는 다음과 같이 엄격 검사를 실행합니다.

```powershell
python evaluation/scripts/validate_dataset.py --require-videos
```

검사는 CSV 구조, 40개 고유 ID, 파일명, 예상 이벤트 수, 영상 누락, 재생시간 허용 범위를 확인합니다. OpenCV를 사용할 수 있으면 FPS와 해상도도 함께 출력합니다.

## 촬영 이후 작업

영상이 준비되면 다음 개발 단계에서 오프라인 평가기를 연결합니다. 평가기는 영상의 `frame_index / fps`를 시간으로 사용하고, 각 클립마다 추적기·행동 상태·폭염 누적시간을 초기화한 뒤 사건 단위 precision, recall, F1과 ID switch 수를 계산해야 합니다.
