# 다중 카메라 사람 추적 설계

## 처리 흐름

```text
카메라 1 사람 검출
→ local_track_id 생성
→ global_person_id 생성
→ 외형 특징 및 품질 갱신
→ 출구 ROI 진입 즉시 전환 후보 보관
→ 카메라 2 입구 ROI에 새 트랙 등장
→ 외형·시간·방향·품질 복합 비교
→ 임계값 통과 시 기존 global_person_id 승계
```

카메라별 `CameraPersonTracker`는 바운딩 박스 IoU, 중심점 거리와 외형 유사도를 이용해 프레임 간 로컬 트랙을 연결합니다. 카메라 서비스들은 현장별 `GlobalIdentityManager`를 공유하므로, 서로 다른 분석 스레드에서도 전환 후보를 안전하게 비교할 수 있습니다.

## 외형 특징과 품질

사람 crop에서 공식 FastReID Market1501 BoT ResNet-50의 2048차원 특징을 추출하고, 4개 수평 구간의 HSV 히스토그램과 전체 Lab 히스토그램을 결합합니다. 품질 점수에는 crop 크기, Laplacian 선명도, 밝기 적정성과 검출 confidence가 반영됩니다.

FastReID 공식 체크포인트는 `python -m scripts.download_fastreid_weights`로 준비합니다. 전체 FastReID 패키지의 선택적 Cython 구성요소에 의존하지 않는 호환 추론기를 사용하므로 Windows와 Linux JupyterHub에서 같은 코드가 동작합니다. 가중치가 없으면 YOLO 특징으로 자동 폴백합니다.

## 전환 후보와 매칭

트랙의 발 좌표가 `camera_exit` ROI에 들어오는 즉시 전환 후보로 등록합니다. 따라서 원본 카메라에서 트랙이 완전히 사라지기 전에 다른 카메라가 먼저 검출해도 매칭할 수 있습니다. 다른 카메라의 `camera_entry` ROI 안에서 생성된 트랙만 후보와 비교하며, 동일 카메라에서 재등장한 경우에는 카메라 간 전환으로 처리하지 않습니다.

총점은 외형 0.65, 이동 시간 0.15, 방향 0.12, 품질 0.08의 가중합입니다. 출구에서는 ROI 중심 쪽 이동, 입구에서는 ROI 중심에서 멀어지는 이동을 기대 방향으로 평가합니다. 관측이 한 프레임뿐이라 방향을 알 수 없으면 중립 점수 0.5를 사용합니다.

매칭 성공 시 작업자 상태에 다음 진단 정보가 포함됩니다.

- `global_person_id`, `local_track_id`
- `matched_from_camera_id`
- `transition_seconds`
- `reid_similarity`
- `direction_score`, `quality_score`, `match_score`

## ROI 구성

구역 편집기에서 카메라별로 다음 유형을 저장할 수 있습니다.

- `camera_entry`: 다른 카메라에서 이동해 온 사람이 처음 나타나는 영역
- `camera_exit`: 다른 카메라로 이동할 사람이 사라지는 영역
- `no_entry`, `fall_risk`, `heavy_equip`: 안전 판정 영역

전환 ROI는 안전 경고 판정에서 제외되며, 카메라별로 독립 저장됩니다.

## 한계와 운영 전환

- 비슷한 작업복을 입은 사람이 동시에 이동하면 색상 기반 descriptor만으로 혼동될 수 있습니다.
- 카메라 시간 동기화가 맞아야 이동 시간 신호가 유효합니다.
- 출구·입구 ROI는 실제 동선에 맞게 충분히 좁게 설정해야 합니다.
- 운영 정확도를 위해 학습형 Re-ID 모델, 카메라 토폴로지별 이동 시간 범위, GPU batch inference와 장기 ID 저장을 추가하는 것이 좋습니다.
