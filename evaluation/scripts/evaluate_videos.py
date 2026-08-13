"""Run zone-excluded offline evaluation over the 40 recorded clips."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EVALUATION_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = EVALUATION_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DEFAULT_ANNOTATIONS = EVALUATION_DIR / "annotations" / "clip_annotations.csv"
DEFAULT_VIDEOS = EVALUATION_DIR / "videos"
DEFAULT_HEAT_FIXTURES = EVALUATION_DIR / "configs" / "heat_fixtures.json"
DEFAULT_RESULTS = EVALUATION_DIR / "results"
DEFAULT_REPORTS = EVALUATION_DIR / "reports"

ZONE_EVENTS = {
    "zone_intrusion",
    "zone_approach",
    "fall_risk_entry",
    "heavy_equipment_entry",
}
BEHAVIOR_EVENTS = {
    "stagger",
    "sudden_sit",
    "fall",
    "fall_still",
    "heat_stagger",
    "heat_sudden_sit",
    "heat_fall",
    "heat_fall_still",
}
BEHAVIOR_PRIORITY = {
    "NORMAL": 0,
    "STAGGER": 1,
    "SUDDEN_SIT": 2,
    "FALL": 3,
    "FALL_STILL": 4,
}


@dataclass(frozen=True)
class ForcedHeatStatus:
    level: str
    sun_threshold: float = 1.15
    shade_threshold: float = 0.85


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="위험구역을 제외하고 40개 영상을 일괄 평가합니다.",
    )
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--videos-dir", type=Path, default=DEFAULT_VIDEOS)
    parser.add_argument("--heat-fixtures", type=Path, default=DEFAULT_HEAT_FIXTURES)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument(
        "--clip",
        action="append",
        dest="clips",
        help="특정 clip_id만 실행합니다. 여러 번 지정할 수 있습니다.",
    )
    parser.add_argument("--max-clips", type=int, default=None)
    parser.add_argument("--object-infer-every", type=int, default=3)
    parser.add_argument("--pose-infer-every", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument(
        "--skip-pose",
        action="store_true",
        help="연결 확인용으로 포즈 추론을 생략합니다. 행동 지표는 유효하지 않습니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="영상 매칭과 제외 대상만 확인하고 모델 추론은 하지 않습니다.",
    )
    parser.add_argument(
        "--resume-from",
        action="append",
        type=Path,
        default=[],
        help="이전 metrics/checkpoint JSON의 완료 클립을 재사용합니다.",
    )
    parser.add_argument(
        "--output-prefix",
        default="zone_excluded",
        help="결과 파일명 접두사입니다.",
    )
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_heat_fixtures(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_resume_results(paths: list[Path]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = json.loads(path.resolve().read_text(encoding="utf-8"))
        for result in payload.get("clips", []):
            results[result["clip_id"]] = result
    return results


def resolve_video_path(
    row: dict[str, str],
    videos_dir: Path,
) -> Path | None:
    """Resolve exact manifest names and the recorded C001-prefixed names."""
    clip_id = row["clip_id"]
    file_name = row["file_name"]
    candidates = [
        videos_dir / file_name,
        videos_dir / f"{clip_id}{file_name}",
        videos_dir / f"{clip_id}_{file_name}",
        videos_dir / f"{clip_id}-{file_name}",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    matches = sorted(videos_dir.glob(f"{clip_id}*.mp4"))
    return matches[0] if len(matches) == 1 else None


def expected_events(row: dict[str, str]) -> list[str]:
    raw = row.get("expected_events", "none")
    return [] if raw == "none" else raw.split("|")


def is_zone_only_clip(row: dict[str, str]) -> bool:
    events = set(expected_events(row))
    return bool(events & ZONE_EVENTS) or row.get("scenario_action") in {
        "enter_zone",
        "approach_zone",
    }


def fixture_in_heat(
    fixture: dict[str, Any],
    elapsed_sec: float,
) -> bool:
    for segment in fixture.get("segments", []):
        start = float(segment.get("start_sec", 0))
        end = segment.get("end_sec")
        if elapsed_sec >= start and (end is None or elapsed_sec < float(end)):
            return bool(segment.get("in_heat", False))
    return False


def most_common_int(values: list[int]) -> int:
    if not values:
        return 0
    counts = Counter(values)
    return max(counts, key=lambda value: (counts[value], value))


def predicted_behavior(states: set[str]) -> str:
    if not states:
        return "NORMAL"
    return max(states, key=lambda state: BEHAVIOR_PRIORITY.get(state, -1))


def helmet_prediction(true_count: int, false_count: int) -> str:
    if true_count == 0 and false_count == 0:
        return "unknown"
    return "helmet" if true_count >= false_count else "no_helmet"


def safe_div(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": safe_div(2 * precision * recall, precision + recall),
    }


def class_metrics(
    expected: list[str],
    predicted: list[str],
) -> dict[str, Any]:
    labels = sorted(set(expected) | set(predicted))
    confusion = {
        label: {candidate: 0 for candidate in labels}
        for label in labels
    }
    for truth, guess in zip(expected, predicted):
        confusion[truth][guess] += 1

    per_class: dict[str, Any] = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[truth][label] for truth in labels if truth != label)
        fn = sum(confusion[label][guess] for guess in labels if guess != label)
        per_class[label] = prf(tp, fp, fn)

    return {
        "count": len(expected),
        "accuracy": safe_div(
            sum(truth == guess for truth, guess in zip(expected, predicted)),
            len(expected),
        ),
        "macro_f1": safe_div(
            sum(float(per_class[label]["f1"]) for label in labels),
            len(labels),
        ),
        "labels": labels,
        "confusion_matrix": confusion,
        "per_class": per_class,
    }


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    included = [result for result in results if not result["excluded"]]

    helmet_rows = [
        result for result in included
        if result["test_group"] == "helmet_zone"
    ]
    helmet_tp = helmet_fp = helmet_fn = helmet_tn = 0
    helmet_correct = 0
    helmet_known = 0
    for result in helmet_rows:
        truth_positive = not result["expected_helmet_on"]
        prediction = result["predicted_helmet"]
        predicted_positive = prediction == "no_helmet"
        if prediction != "unknown":
            helmet_known += 1
        helmet_correct += int(
            (prediction == "helmet" and not truth_positive)
            or (prediction == "no_helmet" and truth_positive)
        )
        if truth_positive and predicted_positive:
            helmet_tp += 1
        elif truth_positive:
            helmet_fn += 1
        elif predicted_positive:
            helmet_fp += 1
        else:
            helmet_tn += 1
    helmet = {
        "count": len(helmet_rows),
        "positive_class": "no_helmet",
        "known_coverage": safe_div(helmet_known, len(helmet_rows)),
        "clip_accuracy_including_unknown": safe_div(helmet_correct, len(helmet_rows)),
        "tn": helmet_tn,
        **prf(helmet_tp, helmet_fp, helmet_fn),
    }

    behavior_rows = [
        result for result in included
        if result["test_group"] in {"behavior", "heat_context"}
    ]
    behavior = class_metrics(
        [result["expected_behavior_state"] for result in behavior_rows],
        [result["predicted_behavior_state"] for result in behavior_rows],
    )

    expected_event_counts: Counter[str] = Counter()
    predicted_event_counts: Counter[str] = Counter()
    for result in behavior_rows:
        expected_event_counts.update(
            event for event in result["expected_events"]
            if event in BEHAVIOR_EVENTS
        )
        predicted_event_counts.update({
            event: count
            for event, count in result["predicted_event_counts"].items()
            if event in BEHAVIOR_EVENTS
        })
    event_labels = sorted(set(expected_event_counts) | set(predicted_event_counts))
    event_per_type: dict[str, Any] = {}
    event_tp = event_fp = event_fn = 0
    for event in event_labels:
        expected_count = expected_event_counts[event]
        predicted_count = predicted_event_counts[event]
        tp = min(expected_count, predicted_count)
        fp = max(0, predicted_count - expected_count)
        fn = max(0, expected_count - predicted_count)
        event_tp += tp
        event_fp += fp
        event_fn += fn
        event_per_type[event] = {
            "expected": expected_count,
            "predicted": predicted_count,
            **prf(tp, fp, fn),
        }
    events = {
        "unit": "person_episode",
        **prf(event_tp, event_fp, event_fn),
        "per_type": event_per_type,
    }

    person_errors = [
        abs(result["predicted_person_count"] - result["expected_person_count"])
        for result in included
    ]
    person_count = {
        "count": len(included),
        "exact_accuracy": safe_div(
            sum(error == 0 for error in person_errors),
            len(person_errors),
        ),
        "mae": safe_div(sum(person_errors), len(person_errors)),
    }

    heat_rows = [
        result for result in included
        if result["test_group"] == "heat_context"
    ]
    heat = {
        "count": len(heat_rows),
        "rest_needed_accuracy": safe_div(
            sum(
                result["observed_rest_needed"] == result["expected_rest_needed"]
                for result in heat_rows
            ),
            len(heat_rows),
        ),
        "timer_reset_accuracy": safe_div(
            sum(
                result["observed_timer_reset"] == result["expected_timer_reset"]
                for result in heat_rows
            ),
            len(heat_rows),
        ),
    }

    tracking_rows = [
        result for result in included
        if result["expected_id_switch_count"] is not None
    ]
    tracking = {
        "count": len(tracking_rows),
        "metric": "track_fragmentation_proxy",
        "exact_accuracy": safe_div(
            sum(
                result["predicted_id_switch_proxy"]
                == result["expected_id_switch_count"]
                for result in tracking_rows
            ),
            len(tracking_rows),
        ),
        "total_expected": sum(
            result["expected_id_switch_count"] for result in tracking_rows
        ),
        "total_predicted": sum(
            result["predicted_id_switch_proxy"] for result in tracking_rows
        ),
    }

    return {
        "dataset": {
            "manifest_clips": len(results),
            "evaluated_clips": len(included),
            "zone_excluded_clips": sum(result["excluded"] for result in results),
            "zone_excluded_ids": [
                result["clip_id"] for result in results if result["excluded"]
            ],
        },
        "helmet": helmet,
        "behavior": behavior,
        "events": events,
        "person_count": person_count,
        "heat_context": heat,
        "tracking": tracking,
        "limitations": [
            "위험구역 관련 클립과 이벤트는 모든 지표의 분자와 분모에서 제외했습니다.",
            "바운딩박스 정답이 없어 mAP@50 및 mAP@50:95는 계산하지 않습니다.",
            "프레임별 실제 인물 ID 정답이 없어 MOTA, IDF1, HOTA 대신 track fragmentation proxy를 사용합니다.",
            "안전모 지표는 클립 내 알려진 작업자 상태의 다수결로 계산하며 unknown coverage를 별도 보고합니다.",
        ],
    }


def analyze_clip(
    row: dict[str, str],
    video_path: Path,
    heat_fixtures: dict[str, Any],
    object_infer_every: int,
    pose_infer_every: int,
    skip_pose: bool,
    batch_size: int,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    cv2 = runtime["cv2"]
    detector = runtime["detector"]
    pose_detector = runtime["pose_detector"]
    process_frame = runtime["process_frame"]
    CameraPersonTracker = runtime["CameraPersonTracker"]
    HeatExposureTracker = runtime["HeatExposureTracker"]
    pose_behavior_detector = runtime["pose_behavior_detector"]

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    tracker = CameraPersonTracker()
    heat_tracker = HeatExposureTracker()
    pose_behavior_detector.reset()

    fixture_name = row.get("heat_fixture", "none")
    fixture = heat_fixtures[fixture_name]
    needs_pose = row["test_group"] in {"behavior", "heat_context"} and not skip_pose
    detections: list[dict[str, Any]] = []
    pose_detections: list[Any] = []
    frame_index = 0
    person_counts: list[int] = []
    unique_track_ids: set[str] = set()
    helmet_true = 0
    helmet_false = 0
    behavior_states: set[str] = set()
    event_keys: set[tuple[str, str]] = set()
    observed_rest_needed = False
    observed_timer_reset = False
    started_at = time.monotonic()

    raw_batch_size = max(1, batch_size) * max(
        1,
        object_infer_every,
        pose_infer_every if needs_pose else 1,
    )
    while True:
        frames = []
        for _ in range(raw_batch_size):
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
        if not frames:
            break

        object_positions = [
            local_index
            for local_index in range(len(frames))
            if (frame_index + local_index) % max(1, object_infer_every) == 0
        ]
        object_outputs = detector.detect_batch(
            [frames[index] for index in object_positions]
        )
        object_by_position = dict(zip(object_positions, object_outputs))

        pose_by_position = {}
        if needs_pose:
            pose_positions = [
                local_index
                for local_index in range(len(frames))
                if (frame_index + local_index) % max(1, pose_infer_every) == 0
            ]
            pose_outputs = pose_detector.detect_batch(
                [frames[index] for index in pose_positions]
            )
            pose_by_position = dict(zip(pose_positions, pose_outputs))

        for local_index, frame in enumerate(frames):
            if local_index in object_by_position:
                detections = object_by_position[local_index]
            if needs_pose and local_index in pose_by_position:
                pose_detections = pose_by_position[local_index]
            elif not needs_pose:
                pose_detections = []

            elapsed = frame_index / fps
            in_heat = fixture_in_heat(fixture, elapsed)
            heat_status = ForcedHeatStatus(level="severe" if in_heat else "inactive")
            _, events, status = process_frame(
                frame,
                detections,
                [],
                width,
                height,
                tracker,
                include_status=True,
                is_outdoor=True,
                heat_status=heat_status,
                heat_exposure_tracker=heat_tracker,
                pose_detections=pose_detections,
                timestamp=1000.0 + elapsed,
                render_overlay=False,
            )

            workers = status["workers"]
            person_counts.append(status["worker_count"])
            for worker in workers:
                unique_track_ids.add(worker["track_id"])
                if worker["helmet_on"] is True:
                    helmet_true += 1
                elif worker["helmet_on"] is False:
                    helmet_false += 1
                behavior_states.add(worker["behavior_state"])
                observed_rest_needed = (
                    observed_rest_needed or bool(worker["rest_needed"])
                )
                if (
                    row.get("heat_fixture") == "heat_reset_reentry"
                    and 13.0 <= elapsed < 14.0
                    and not worker["in_heat_zone"]
                    and float(worker["heat_seconds"]) <= 0.1
                ):
                    observed_timer_reset = True

            for event in events:
                event_type = event.get("type")
                if event_type in BEHAVIOR_EVENTS:
                    event_keys.add((event_type, str(event.get("track_id", ""))))

            frame_index += 1

    capture.release()
    predicted_event_counts = Counter(event for event, _ in event_keys)
    expected_switch_raw = row.get("expected_id_switch_count", "")
    expected_switch = int(expected_switch_raw) if expected_switch_raw else None
    predicted_count = most_common_int(person_counts)

    return {
        "clip_id": row["clip_id"],
        "file_name": row["file_name"],
        "resolved_file_name": video_path.name,
        "test_group": row["test_group"],
        "excluded": False,
        "exclude_reason": "",
        "fps": round(fps, 3),
        "frame_count": frame_index,
        "duration_sec": round(frame_index / fps, 3) if fps else 0.0,
        "processing_sec": round(time.monotonic() - started_at, 3),
        "expected_helmet_on": row["helmet_on"] == "true",
        "predicted_helmet": helmet_prediction(helmet_true, helmet_false),
        "helmet_known_observations": helmet_true + helmet_false,
        "helmet_unknown_observations": max(
            0,
            sum(person_counts) - helmet_true - helmet_false,
        ),
        "expected_behavior_state": row["expected_behavior_state"],
        "predicted_behavior_state": predicted_behavior(behavior_states),
        "expected_events": expected_events(row),
        "predicted_event_counts": dict(sorted(predicted_event_counts.items())),
        "expected_rest_needed": row["expected_rest_needed"] == "true",
        "observed_rest_needed": observed_rest_needed,
        "expected_timer_reset": row["expected_timer_reset"] == "true",
        "observed_timer_reset": observed_timer_reset,
        "expected_person_count": int(row["expected_person_count"]),
        "predicted_person_count": predicted_count,
        "tracking_expectation": row["tracking_expectation"],
        "expected_id_switch_count": expected_switch,
        "predicted_id_switch_proxy": max(
            0,
            len(unique_track_ids) - int(row["expected_person_count"]),
        ),
        "unique_track_count": len(unique_track_ids),
    }


def excluded_result(row: dict[str, str], video_path: Path) -> dict[str, Any]:
    return {
        "clip_id": row["clip_id"],
        "file_name": row["file_name"],
        "resolved_file_name": video_path.name,
        "test_group": row["test_group"],
        "excluded": True,
        "exclude_reason": "zone_related_clip",
        "expected_helmet_on": row["helmet_on"] == "true",
        "predicted_helmet": "excluded",
        "expected_behavior_state": row["expected_behavior_state"],
        "predicted_behavior_state": "excluded",
        "expected_events": expected_events(row),
        "predicted_event_counts": {},
        "expected_rest_needed": row["expected_rest_needed"] == "true",
        "observed_rest_needed": False,
        "expected_timer_reset": row["expected_timer_reset"] == "true",
        "observed_timer_reset": False,
        "expected_person_count": int(row["expected_person_count"]),
        "predicted_person_count": 0,
        "tracking_expectation": row["tracking_expectation"],
        "expected_id_switch_count": None,
        "predicted_id_switch_proxy": 0,
        "unique_track_count": 0,
    }


def write_predictions(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "clip_id",
        "file_name",
        "resolved_file_name",
        "test_group",
        "excluded",
        "exclude_reason",
        "expected_helmet_on",
        "predicted_helmet",
        "helmet_known_observations",
        "helmet_unknown_observations",
        "expected_behavior_state",
        "predicted_behavior_state",
        "expected_events",
        "predicted_event_counts",
        "expected_rest_needed",
        "observed_rest_needed",
        "expected_timer_reset",
        "observed_timer_reset",
        "expected_person_count",
        "predicted_person_count",
        "tracking_expectation",
        "expected_id_switch_count",
        "predicted_id_switch_proxy",
        "unique_track_count",
        "fps",
        "frame_count",
        "duration_sec",
        "processing_sec",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            row = dict(result)
            row["expected_events"] = "|".join(row["expected_events"]) or "none"
            row["predicted_event_counts"] = json.dumps(
                row["predicted_event_counts"],
                ensure_ascii=False,
                sort_keys=True,
            )
            writer.writerow(row)


def pct(value: float | int) -> str:
    return f"{float(value) * 100:.1f}%"


def markdown_report(metrics: dict[str, Any]) -> str:
    dataset = metrics["dataset"]
    helmet = metrics["helmet"]
    behavior = metrics["behavior"]
    events = metrics["events"]
    people = metrics["person_count"]
    heat = metrics["heat_context"]
    tracking = metrics["tracking"]
    excluded = ", ".join(dataset["zone_excluded_ids"]) or "없음"

    lines = [
        "# 위험구역 제외 영상 성능 평가",
        "",
        "## 평가 범위",
        "",
        f"- 정답표 영상: {dataset['manifest_clips']}개",
        f"- 실제 평가: {dataset['evaluated_clips']}개",
        f"- 위험구역 제외: {dataset['zone_excluded_clips']}개 ({excluded})",
        "- 위험구역 이벤트는 종합지표의 분자와 분모에 포함하지 않음",
        "",
        "## 요약 지표",
        "",
        "| 영역 | 지표 | 결과 |",
        "|---|---|---:|",
        f"| 안전모 | 미착용 precision | {pct(helmet['precision'])} |",
        f"| 안전모 | 미착용 recall | {pct(helmet['recall'])} |",
        f"| 안전모 | 미착용 F1 | {pct(helmet['f1'])} |",
        f"| 안전모 | known coverage | {pct(helmet['known_coverage'])} |",
        f"| 행동 | clip accuracy | {pct(behavior['accuracy'])} |",
        f"| 행동 | macro F1 | {pct(behavior['macro_f1'])} |",
        f"| 사건 | episode precision | {pct(events['precision'])} |",
        f"| 사건 | episode recall | {pct(events['recall'])} |",
        f"| 사건 | episode F1 | {pct(events['f1'])} |",
        f"| 인원수 | exact accuracy | {pct(people['exact_accuracy'])} |",
        f"| 인원수 | MAE | {people['mae']:.3f} |",
        f"| 폭염 | 휴식 권고 정확도 | {pct(heat['rest_needed_accuracy'])} |",
        f"| 폭염 | 타이머 초기화 정확도 | {pct(heat['timer_reset_accuracy'])} |",
        f"| 추적 | fragmentation proxy 정확도 | {pct(tracking['exact_accuracy'])} |",
        "",
        "## 사건별 지표",
        "",
        "| 사건 | 정답 | 예측 | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for event, values in events["per_type"].items():
        lines.append(
            f"| {event} | {values['expected']} | {values['predicted']} | "
            f"{pct(values['precision'])} | {pct(values['recall'])} | {pct(values['f1'])} |"
        )

    lines.extend(["", "## 제한사항", ""])
    lines.extend(f"- {item}" for item in metrics["limitations"])
    lines.append("")
    return "\n".join(lines)


def load_runtime(skip_pose: bool) -> dict[str, Any]:
    os.chdir(BACKEND_DIR)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    import cv2
    from app.services.detector import detector
    from app.services.person_tracking import CameraPersonTracker, HeatExposureTracker
    from app.services.pipeline import process_frame
    from app.services.pose_behavior_detector import pose_behavior_detector
    from app.services.pose_detector import pose_detector

    if not skip_pose and not pose_detector.available:
        raise RuntimeError(
            "POSE_ENABLED=1과 유효한 POSE_MODEL_PATH가 필요합니다. "
            "연결 확인만 하려면 --skip-pose를 사용하세요."
        )
    return {
        "cv2": cv2,
        "detector": detector,
        "pose_detector": pose_detector,
        "process_frame": process_frame,
        "CameraPersonTracker": CameraPersonTracker,
        "HeatExposureTracker": HeatExposureTracker,
        "pose_behavior_detector": pose_behavior_detector,
    }


def main() -> int:
    args = parse_args()
    rows = load_rows(args.annotations.resolve())
    heat_fixtures = load_heat_fixtures(args.heat_fixtures.resolve())
    videos_dir = args.videos_dir.resolve()

    if args.clips:
        selected = set(args.clips)
        rows = [row for row in rows if row["clip_id"] in selected]
        missing_ids = sorted(selected - {row["clip_id"] for row in rows})
        if missing_ids:
            print(f"[오류] 정답표에 없는 clip_id: {', '.join(missing_ids)}")
            return 2
    if args.max_clips is not None:
        rows = rows[: max(0, args.max_clips)]

    resolved: list[tuple[dict[str, str], Path]] = []
    missing_videos: list[str] = []
    for row in rows:
        path = resolve_video_path(row, videos_dir)
        if path is None:
            missing_videos.append(f"{row['clip_id']}:{row['file_name']}")
        else:
            resolved.append((row, path))
    if missing_videos:
        print(f"[오류] 영상 {len(missing_videos)}개를 찾을 수 없습니다.")
        for item in missing_videos[:10]:
            print(f"  - {item}")
        return 2

    excluded_count = sum(is_zone_only_clip(row) for row, _ in resolved)
    print(
        f"영상 매칭 {len(resolved)}개 완료 · "
        f"위험구역 제외 {excluded_count}개 · 평가 {len(resolved) - excluded_count}개"
    )
    if args.dry_run:
        for row, path in resolved:
            state = "제외" if is_zone_only_clip(row) else "평가"
            print(f"[{state}] {row['clip_id']} -> {path.name}")
        return 0

    resume_results = load_resume_results(args.resume_from)
    if resume_results:
        print(f"이전 결과 {len(resume_results)}개를 재사용합니다.")

    args.results_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = (
        args.results_dir / f"checkpoint_{args.output_prefix}.json"
    )
    runtime = None
    results: list[dict[str, Any]] = []
    total = len(resolved)
    for index, (row, video_path) in enumerate(resolved, start=1):
        if row["clip_id"] in resume_results:
            result = resume_results[row["clip_id"]]
            print(f"[{index:02d}/{total:02d}] {row['clip_id']} 이전 결과 재사용")
        elif is_zone_only_clip(row):
            result = excluded_result(row, video_path)
            print(f"[{index:02d}/{total:02d}] {row['clip_id']} 위험구역 제외")
        else:
            if runtime is None:
                runtime = load_runtime(args.skip_pose)
            print(f"[{index:02d}/{total:02d}] {row['clip_id']} 분석 시작: {video_path.name}")
            result = analyze_clip(
                row,
                video_path,
                heat_fixtures,
                args.object_infer_every,
                args.pose_infer_every,
                args.skip_pose,
                args.batch_size,
                runtime,
            )
            print(
                f"           완료 {result['processing_sec']:.1f}s · "
                f"인원 {result['predicted_person_count']} · "
                f"행동 {result['predicted_behavior_state']}"
            )
        results.append(result)
        checkpoint_payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "zone_policy": "excluded",
            "config": {
                "object_infer_every": args.object_infer_every,
                "pose_infer_every": args.pose_infer_every,
                "skip_pose": args.skip_pose,
                "batch_size": args.batch_size,
            },
            "clips": results,
        }
        checkpoint_tmp = checkpoint_path.with_suffix(".tmp")
        checkpoint_tmp.write_text(
            json.dumps(checkpoint_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        checkpoint_tmp.replace(checkpoint_path)

    metrics = aggregate_metrics(results)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "zone_policy": "excluded",
        "config": {
            "object_infer_every": args.object_infer_every,
            "pose_infer_every": args.pose_infer_every,
            "skip_pose": args.skip_pose,
            "batch_size": args.batch_size,
        },
        "metrics": metrics,
        "clips": results,
    }

    args.results_dir.mkdir(parents=True, exist_ok=True)
    args.reports_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix
    predictions_path = args.results_dir / f"predictions_{prefix}.csv"
    metrics_path = args.results_dir / f"metrics_{prefix}.json"
    report_path = args.reports_dir / f"metrics_{prefix}.md"
    write_predictions(predictions_path, results)
    metrics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path.write_text(markdown_report(metrics), encoding="utf-8")

    print("")
    print("평가 완료")
    print(f"- 예측 원본: {predictions_path}")
    print(f"- 지표 JSON: {metrics_path}")
    print(f"- 요약 보고서: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
