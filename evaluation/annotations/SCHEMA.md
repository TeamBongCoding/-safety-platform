# clip_annotations.csv 정의

CSV는 영상 한 개당 한 행을 사용합니다. 모든 파일은 UTF-8이며 쉼표가 포함된 설명은 큰따옴표로 감쌉니다.

## 열 정의

| 열 | 의미 |
|---|---|
| `clip_id` | 고유 영상 ID. `C001`부터 `C040`까지 사용 |
| `file_name` | `evaluation/videos/`에 저장할 정확한 MP4 파일명 |
| `test_group` | `helmet_zone`, `behavior`, `heat_context`, `tracking` 중 하나 |
| `target_duration_sec` | 목표 영상 길이(초) |
| `duration_tolerance_sec` | 허용되는 길이 오차(초) |
| `scenario_action` | 사람이 실제로 연출하는 행동 |
| `expected_behavior_state` | 모델이 최종적으로 맞혀야 하는 상태: `NORMAL`, `SUDDEN_SIT`, `FALL`, `FALL_STILL` |
| `in_heat_context` | `true`, `false`, `mixed`. 실제 날씨가 아니라 평가기가 주입할 폭염 맥락 |
| `helmet_on` | 영상 속 모든 대상자의 안전모 착용 여부 |
| `zone_type` | `general`, `work_area`, `no_entry` |
| `zone_status` | 발 위치 기준 `outside`, `near`, `inside` |
| `expected_events` | 기대 이벤트. 여러 이벤트는 `|`로 연결하고 없으면 `none` |
| `expected_event_count` | 기대하는 사람별 사건 총수 |
| `event_count_unit` | `person_episode`: 같은 사람이 연속으로 발생시킨 같은 이벤트는 한 건으로 계산 |
| `expected_rest_needed` | 10초 이상 폭염 노출 후 휴식 권고 상태가 켜져야 하는지 여부 |
| `expected_timer_reset` | 비폭염 상태 10초 이후 누적시간이 0으로 초기화되어야 하는지 여부 |
| `expected_person_count` | 영상에 등장하는 정답 인원 수 |
| `expected_id_switch_count` | 허용되는 ID 변경 횟수. 채점하지 않으면 빈칸 |
| `tracking_expectation` | 추적 채점 방식 |
| `zone_fixture` | 평가 시 연결할 구역 설정 이름 |
| `heat_fixture` | `configs/heat_fixtures.json`의 설정 이름 |
| `recording_summary` | 촬영 목적을 한 문장으로 요약 |

## 사건 수 계산

파이프라인은 같은 구역 경고를 여러 프레임에서 반복 출력할 수 있고 DB는 별도 쿨다운을 적용합니다. 이 CSV의 정답 수는 프레임 수나 DB 행 수가 아니라 `person_episode`를 사용합니다.

예를 들어 한 사람이 작업구역 안에서 5초 동안 안전모를 쓰지 않았다면 `no_helmet`은 1건입니다. 두 사람이 같은 행동을 했다면 2건입니다.

`fall_still`은 그 전에 `fall` 상태를 거치므로 C028은 `fall|fall_still`, C034는 `heat_fall|heat_fall_still` 두 사건을 기대합니다.

## 상태와 이벤트의 차이

- `rest_needed`는 이벤트가 아니라 작업자 상태입니다.
- `heat_timer_reset`은 이벤트가 아니라 누적시간 동작입니다.
- 정상 걷기, 천천히 앉기, 물건 줍기, 허리 숙이기는 촬영 행동은 서로 다르지만 기대 모델 상태는 모두 `NORMAL`입니다.

## 구역 판정

구역 판정은 사람 바운딩박스의 발 위치와 정규화된 폴리곤을 사용합니다. 촬영할 때 테이프로 경계를 보이게 하고, 영상이 완성되면 첫 프레임에 맞춰 실제 폴리곤 좌표를 확정합니다. `near` 영상은 테이프를 밟거나 넘지 않고 경계 바로 밖을 지나가야 합니다.
