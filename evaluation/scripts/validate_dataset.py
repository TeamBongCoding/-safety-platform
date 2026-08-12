"""Validate the evaluation manifest and, when present, video metadata."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


EVALUATION_DIR = Path(__file__).resolve().parents[1]
ANNOTATION_PATH = EVALUATION_DIR / "annotations" / "clip_annotations.csv"
VIDEO_DIR = EVALUATION_DIR / "videos"
HEAT_FIXTURE_PATH = EVALUATION_DIR / "configs" / "heat_fixtures.json"

EXPECTED_COLUMNS = [
    "clip_id",
    "file_name",
    "test_group",
    "target_duration_sec",
    "duration_tolerance_sec",
    "scenario_action",
    "expected_behavior_state",
    "in_heat_context",
    "helmet_on",
    "zone_type",
    "zone_status",
    "expected_events",
    "expected_event_count",
    "event_count_unit",
    "expected_rest_needed",
    "expected_timer_reset",
    "expected_person_count",
    "expected_id_switch_count",
    "tracking_expectation",
    "zone_fixture",
    "heat_fixture",
    "recording_summary",
]

VALID_TEST_GROUPS = {"helmet_zone", "behavior", "heat_context", "tracking"}
VALID_BEHAVIORS = {"NORMAL", "SUDDEN_SIT", "FALL", "FALL_STILL"}
VALID_HEAT_CONTEXTS = {"true", "false", "mixed"}
VALID_BOOLEANS = {"true", "false"}
VALID_ZONE_TYPES = {"general", "work_area", "no_entry"}
VALID_ZONE_STATUSES = {"outside", "near", "inside"}
VALID_ZONE_FIXTURES = {"none", "work_area", "no_entry"}
VALID_TRACKING = {
    "not_applicable",
    "same_id",
    "same_id_each_person",
    "new_id_allowed",
}
VALID_EVENTS = {
    "none",
    "no_helmet",
    "zone_intrusion",
    "zone_approach",
    "sudden_sit",
    "fall",
    "fall_still",
    "heat_sudden_sit",
    "heat_fall",
    "heat_fall_still",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="평가 CSV와 촬영 영상 40개를 검사합니다.")
    parser.add_argument(
        "--require-videos",
        action="store_true",
        help="영상이 하나라도 없으면 실패 처리합니다.",
    )
    return parser.parse_args()


def load_rows(errors: list[str]) -> list[dict[str, str]]:
    try:
        with ANNOTATION_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != EXPECTED_COLUMNS:
                errors.append(
                    "CSV 열이 정의와 다릅니다.\n"
                    f"  기대: {EXPECTED_COLUMNS}\n"
                    f"  실제: {reader.fieldnames}"
                )
            return list(reader)
    except FileNotFoundError:
        errors.append(f"정답 CSV가 없습니다: {ANNOTATION_PATH}")
        return []


def as_int(row: dict[str, str], key: str, errors: list[str]) -> int | None:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError):
        errors.append(f"{row.get('clip_id', '?')}: {key}는 정수여야 합니다.")
        return None


def validate_manifest(rows: list[dict[str, str]], errors: list[str]) -> None:
    if len(rows) != 40:
        errors.append(f"CSV 행 수는 40개여야 하지만 현재 {len(rows)}개입니다.")

    expected_ids = [f"C{index:03d}" for index in range(1, 41)]
    actual_ids = [row.get("clip_id", "") for row in rows]
    if actual_ids != expected_ids:
        errors.append("clip_id는 C001부터 C040까지 순서대로 있어야 합니다.")

    duplicate_ids = [key for key, count in Counter(actual_ids).items() if count > 1]
    file_names = [row.get("file_name", "") for row in rows]
    duplicate_files = [key for key, count in Counter(file_names).items() if count > 1]
    if duplicate_ids:
        errors.append(f"중복 clip_id: {duplicate_ids}")
    if duplicate_files:
        errors.append(f"중복 file_name: {duplicate_files}")

    try:
        heat_fixtures = json.loads(HEAT_FIXTURE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        errors.append(f"폭염 fixture를 읽을 수 없습니다: {exc}")
        heat_fixtures = {}

    for row in rows:
        clip_id = row.get("clip_id", "?")
        file_name = row.get("file_name", "")
        if Path(file_name).name != file_name or not file_name.endswith(".mp4"):
            errors.append(f"{clip_id}: file_name은 경로 없는 소문자 .mp4 파일명이어야 합니다.")
        if file_name != file_name.lower() or " " in file_name:
            errors.append(f"{clip_id}: file_name에는 대문자나 공백을 사용할 수 없습니다.")
        if row.get("test_group") not in VALID_TEST_GROUPS:
            errors.append(f"{clip_id}: 잘못된 test_group입니다.")
        if row.get("expected_behavior_state") not in VALID_BEHAVIORS:
            errors.append(f"{clip_id}: 잘못된 expected_behavior_state입니다.")
        if row.get("in_heat_context") not in VALID_HEAT_CONTEXTS:
            errors.append(f"{clip_id}: in_heat_context는 true, false, mixed 중 하나여야 합니다.")
        for key in ("helmet_on", "expected_rest_needed", "expected_timer_reset"):
            if row.get(key) not in VALID_BOOLEANS:
                errors.append(f"{clip_id}: {key}는 true 또는 false여야 합니다.")
        if row.get("zone_type") not in VALID_ZONE_TYPES:
            errors.append(f"{clip_id}: 잘못된 zone_type입니다.")
        if row.get("zone_status") not in VALID_ZONE_STATUSES:
            errors.append(f"{clip_id}: 잘못된 zone_status입니다.")
        if row.get("zone_fixture") not in VALID_ZONE_FIXTURES:
            errors.append(f"{clip_id}: 잘못된 zone_fixture입니다.")
        if row.get("tracking_expectation") not in VALID_TRACKING:
            errors.append(f"{clip_id}: 잘못된 tracking_expectation입니다.")
        if row.get("heat_fixture") not in heat_fixtures:
            errors.append(f"{clip_id}: 존재하지 않는 heat_fixture입니다: {row.get('heat_fixture')}")
        if row.get("event_count_unit") != "person_episode":
            errors.append(f"{clip_id}: event_count_unit은 person_episode여야 합니다.")

        event_names = row.get("expected_events", "").split("|")
        invalid_events = [event for event in event_names if event not in VALID_EVENTS]
        if invalid_events:
            errors.append(f"{clip_id}: 알 수 없는 expected_events: {invalid_events}")

        event_count = as_int(row, "expected_event_count", errors)
        target_duration = as_int(row, "target_duration_sec", errors)
        tolerance = as_int(row, "duration_tolerance_sec", errors)
        person_count = as_int(row, "expected_person_count", errors)
        if target_duration is not None and target_duration <= 0:
            errors.append(f"{clip_id}: target_duration_sec는 양수여야 합니다.")
        if tolerance is not None and tolerance < 0:
            errors.append(f"{clip_id}: duration_tolerance_sec는 음수일 수 없습니다.")
        if person_count is not None and person_count <= 0:
            errors.append(f"{clip_id}: expected_person_count는 양수여야 합니다.")
        if event_count is not None:
            if event_names == ["none"] and event_count != 0:
                errors.append(f"{clip_id}: expected_events=none이면 event_count는 0이어야 합니다.")
            if event_names != ["none"] and event_count <= 0:
                errors.append(f"{clip_id}: 이벤트가 있으면 event_count는 양수여야 합니다.")

        id_switch = row.get("expected_id_switch_count", "")
        if id_switch:
            try:
                if int(id_switch) < 0:
                    raise ValueError
            except ValueError:
                errors.append(f"{clip_id}: expected_id_switch_count는 빈칸 또는 0 이상의 정수입니다.")

        if row.get("expected_rest_needed") == "true" and event_names != ["none"]:
            errors.append(f"{clip_id}: rest_needed는 이벤트가 아니므로 expected_events는 none이어야 합니다.")
        if row.get("expected_timer_reset") == "true" and event_names != ["none"]:
            errors.append(f"{clip_id}: timer_reset은 이벤트가 아니므로 expected_events는 none이어야 합니다.")


def read_video_metadata(path: Path) -> tuple[float, float, int, int] | None:
    try:
        import cv2  # type: ignore
    except ImportError:
        return None

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        return 0.0, 0.0, 0, 0
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    capture.release()
    duration = frame_count / fps if fps > 0 else 0.0
    return duration, fps, width, height


def validate_videos(
    rows: list[dict[str, str]],
    require_videos: bool,
    errors: list[str],
    warnings: list[str],
) -> int:
    missing: list[str] = []
    present_count = 0
    opencv_unavailable = False

    for row in rows:
        path = VIDEO_DIR / row["file_name"]
        if not path.is_file():
            missing.append(row["file_name"])
            continue

        present_count += 1
        metadata = read_video_metadata(path)
        if metadata is None:
            opencv_unavailable = True
            continue
        duration, fps, width, height = metadata
        clip_id = row["clip_id"]
        if duration <= 0 or fps <= 0:
            errors.append(f"{clip_id}: 영상을 열거나 재생시간을 읽을 수 없습니다: {path.name}")
            continue
        target = int(row["target_duration_sec"])
        tolerance = int(row["duration_tolerance_sec"])
        if abs(duration - target) > tolerance:
            errors.append(
                f"{clip_id}: 길이 {duration:.1f}초, 허용 범위 {target - tolerance}~{target + tolerance}초"
            )
        if width <= height:
            errors.append(f"{clip_id}: 가로 영상이어야 합니다. 현재 {width}x{height}")
        if not 24 <= fps <= 31:
            warnings.append(f"{clip_id}: 권장 FPS는 30이며 현재 {fps:.2f} FPS입니다.")

    if missing:
        message = f"영상 {len(missing)}개가 아직 없습니다. 첫 누락: {', '.join(missing[:5])}"
        if require_videos:
            errors.append(message)
        else:
            warnings.append(message)
    if opencv_unavailable and present_count:
        warnings.append("OpenCV가 없어 영상 길이, FPS, 해상도 검사는 생략했습니다.")
    return present_count


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    warnings: list[str] = []
    rows = load_rows(errors)
    if rows:
        validate_manifest(rows, errors)
    present_count = validate_videos(rows, args.require_videos, errors, warnings)

    print(f"정답표: {len(rows)}개 행")
    print(f"영상: {present_count}/40개 준비")
    for warning in warnings:
        print(f"[주의] {warning}")
    for error in errors:
        print(f"[오류] {error}")

    if errors:
        print(f"검사 실패: 오류 {len(errors)}개")
        return 1
    print("검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
