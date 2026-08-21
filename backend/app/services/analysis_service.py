"""영상 분석을 FastAPI 프로세스 안에서 실행하고 최신 결과를 공유한다."""

import json
import logging
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

import cv2
import numpy as np
from shapely.geometry import Polygon
from sqlalchemy import func, select

from ..config import (
    BLE_TAG_ACTIVE_SECONDS,
    EVENT_EPISODE_CLOSE_GAP_SEC,
    EVENT_EPISODE_MIN_DURATION_SEC,
    EVENT_EPISODE_UPDATE_INTERVAL_SEC,
    LIVE_INFER_EVERY,
    LIVE_POSE_INFER_EVERY,
    MODEL_VERSION,
    POSE_INFER_EVERY,
    PROJECT_ROOT,
    RULE_VERSION,
)
from ..database import SessionLocal
from ..models import Event, Zone
from ..time_utils import kst_day_start_utc, kst_now
from .episode_aggregator import EpisodeAggregator, ExposureAccumulator
from .multi_camera_identity import MultiCameraIdentityManager
from .person_tracking import CameraPersonTracker, HeatExposureTracker
from .pose_detector import pose_detector

INFER_EVERY = 3
EVENT_COOLDOWN_SECONDS = 10
ZONE_REFRESH_SECONDS = 1
INPUT_REQUIRED_MESSAGE = "카메라를 설정하거나 녹화된 영상을 업로드해 주세요."
CAMERA_IDS = ("camera-1", "camera-2")
RECEIVER_IDS = ("receiver-1", "receiver-2")
FRAME_BATCH_COALESCE_SECONDS = 0.012


def _new_camera_state(camera_id: str, tracker: CameraPersonTracker | None = None) -> dict:
    return {
        "camera_id": camera_id,
        "tracker": tracker or CameraPersonTracker(),
        "connected": False,
        "pending_jpeg": None,
        "latest_jpeg": None,
        "latest_original_jpeg": None,
        "latest_scene": None,
        "frame_version": 0,
        "frame_index": 0,
        "processing_fps": 0.0,
        "rate_started_at": time.monotonic(),
        "rate_frames": 0,
        "detections": [],
        "pose_detections": [],
        "workers": [],
        "last_error": None,
    }


def should_persist_event(event: dict) -> bool:
    """Apply the product's event-log policy without hiding live UI warnings."""
    if event.get("type") in {"stagger", "heat_stagger"}:
        return False
    if event.get("type") == "no_helmet" and not event.get("in_risk_zone", False):
        return False
    return True


class AnalysisService:
    def __init__(
        self,
        site_id: int,
        external: bool = False,
        is_outdoor: bool = False,
        heat_service=None,
    ):
        self.site_id = site_id
        self.external = external
        self.is_outdoor = is_outdoor
        self._heat_service = heat_service
        self._heat_exposure_tracker = HeatExposureTracker()
        self.person_tracker = CameraPersonTracker()
        self._identity_manager = MultiCameraIdentityManager()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._frame_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_jpeg: bytes | None = None
        self._latest_original_jpeg: bytes | None = None
        self._pending_jpeg: bytes | None = None
        self._external_connected = False
        self._frame_version = 0
        self._event_last_seen: dict[
            tuple[str, int | None, str | None], float
        ] = {}
        self._last_event_cleanup = 0.0
        # BLE observations and bindings are deliberately session-memory only.
        # They are never written to Supabase/SQL and disappear with this service.
        self._ble_tags: dict[str, dict] = {}
        self._camera_states = {
            "camera-1": _new_camera_state("camera-1", self.person_tracker),
            "camera-2": _new_camera_state("camera-2"),
        }
        self._zones: list[dict] = []
        self._episode_aggregator = EpisodeAggregator(
            session_factory=SessionLocal,
            should_persist=should_persist_event,
            close_gap_sec=EVENT_EPISODE_CLOSE_GAP_SEC,
            min_duration_sec=EVENT_EPISODE_MIN_DURATION_SEC,
            update_interval_sec=EVENT_EPISODE_UPDATE_INTERVAL_SEC,
            model_version=MODEL_VERSION,
            rule_version=RULE_VERSION,
        )
        self._exposure_accumulator = ExposureAccumulator(
            site_id=site_id,
            session_factory=SessionLocal,
        )
        self._status = {
            "running": False,
            "stage": "stopped",
            "message": INPUT_REQUIRED_MESSAGE,
            "source": None,
            "frame_index": 0,
            "processing_fps": 0.0,
            "worker_count": 0,
            "unique_person_count": 0,
            "no_helmet_count": 0,
            "workers": [],
            "last_error": None,
        }

    def start(self, video_path: str):
        if self._thread and self._thread.is_alive():
            return

        source = Path(video_path)
        if not source.is_absolute():
            source = (PROJECT_ROOT / source).resolve()

        self._stop_event.clear()
        self._set_status(
            running=True,
            stage="loading",
            message="AI 모델과 영상을 준비하고 있습니다.",
            source=str(source),
            last_error=None,
        )
        self._thread = threading.Thread(
            target=self._run,
            args=(source,),
            name="video-analysis",
            daemon=True,
        )
        self._thread.start()

    def start_external(self):
        if self._thread and self._thread.is_alive():
            return
        self.external = True
        self._stop_event.clear()
        self._set_status(
            running=False,
            stage="waiting_camera",
            message="클라이언트 카메라 연결을 기다리고 있습니다.",
            source="browser",
            last_error=None,
        )
        self._thread = threading.Thread(
            target=self._run_external,
            name=f"browser-camera-analysis-{self.site_id}",
            daemon=True,
        )
        self._thread.start()

    def attach_external_camera(self, camera_id: str = "camera-1") -> bool:
        if camera_id not in CAMERA_IDS:
            return False
        self.start_external()
        with self._lock:
            state = self._camera_states[camera_id]
            if state["connected"]:
                return False
            state.update({
                "connected": True,
                "pending_jpeg": None,
                "latest_jpeg": None,
                "latest_original_jpeg": None,
                "latest_scene": None,
                "frame_version": 0,
                "frame_index": 0,
                "processing_fps": 0.0,
                "rate_started_at": time.monotonic(),
                "rate_frames": 0,
                "detections": [],
                "pose_detections": [],
                "workers": [],
                "last_error": None,
            })
        self._refresh_external_status(
            stage="waiting_frame",
            message=f"{camera_id} 첫 프레임을 기다리고 있습니다.",
        )
        return True

    def detach_external_camera(self, camera_id: str = "camera-1"):
        from .pose_behavior_detector import pose_behavior_detector

        if camera_id not in CAMERA_IDS:
            return
        with self._lock:
            state = self._camera_states[camera_id]
            state["connected"] = False
            state["pending_jpeg"] = None
            state["latest_jpeg"] = None
            state["latest_original_jpeg"] = None
            state["latest_scene"] = None
            state["workers"] = []
            state["tracker"].flush()
            self._identity_manager.forget_camera(camera_id)
            any_connected = any(
                item["connected"] for item in self._camera_states.values()
            )
            if not any_connected:
                self._ble_tags.clear()
        if not any_connected:
            self._identity_manager.flush()
            pose_behavior_detector.reset()
            self._heat_exposure_tracker.flush()
            self._episode_aggregator.flush()
            self._exposure_accumulator.flush()
            with self._lock:
                self._event_last_seen.clear()
            self._set_status(
                running=False,
                stage="camera_disconnected",
                message="카메라 연결이 종료되었습니다.",
                workers=[],
                worker_count=0,
                no_helmet_count=0,
            )
        else:
            self._refresh_external_status()

    def submit_jpeg(self, jpeg: bytes, camera_id: str = "camera-1"):
        if camera_id not in CAMERA_IDS:
            return
        with self._lock:
            state = self._camera_states[camera_id]
            if not state["connected"]:
                return
            state["pending_jpeg"] = jpeg
        self._frame_ready.set()

    def stop(self):
        from .pose_behavior_detector import pose_behavior_detector

        self._stop_event.set()
        self._frame_ready.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        for state in self._camera_states.values():
            state["tracker"].flush()
        self._identity_manager.flush()
        pose_behavior_detector.reset()
        self._heat_exposure_tracker.flush()
        self._episode_aggregator.flush()
        self._exposure_accumulator.flush()
        with self._lock:
            self._ble_tags.clear()
            self._event_last_seen.clear()
            self._last_event_cleanup = 0.0
            for state in self._camera_states.values():
                state["connected"] = False
                state["pending_jpeg"] = None
                state["latest_jpeg"] = None
                state["latest_original_jpeg"] = None
                state["workers"] = []
        self._set_status(
            running=False,
            stage="stopped",
            message=INPUT_REQUIRED_MESSAGE,
        )

    def get_frame(self, camera_id: str | None = None):
        with self._lock:
            if self.external:
                selected = camera_id if camera_id in CAMERA_IDS else "camera-1"
                state = self._camera_states[selected]
                return state["latest_jpeg"], state["frame_version"]
            return self._latest_jpeg, self._frame_version

    def get_original_frame(self, camera_id: str | None = None):
        with self._lock:
            if self.external:
                selected = camera_id if camera_id in CAMERA_IDS else "camera-1"
                state = self._camera_states[selected]
                return state["latest_original_jpeg"], state["frame_version"]
            return self._latest_original_jpeg, self._frame_version

    def get_status(self):
        with self._lock:
            snapshot = dict(self._status)
            snapshot["workers"] = [dict(worker) for worker in self._status["workers"]]
        return snapshot

    def get_summary(self):
        snapshot = self.get_status()
        start_of_day = kst_day_start_utc()
        with SessionLocal() as db:
            violations_today = db.scalar(
                select(func.count(Event.id)).where(
                    Event.site_id == self.site_id,
                    Event.timestamp >= start_of_day,
                )
            ) or 0

        return {
            "type": "summary",
            "source": snapshot["source"],
            "timestamp": kst_now().isoformat(),
            "worker_count": snapshot["worker_count"],
            "no_helmet_count": snapshot["no_helmet_count"],
            "violations_today": violations_today,
            "analysis_running": snapshot["running"],
            "analysis_stage": snapshot["stage"],
            "analysis_message": snapshot["message"],
            "frame_index": snapshot["frame_index"],
            "processing_fps": snapshot["processing_fps"],
            "workers": snapshot["workers"],
            "ble_tags": self.get_ble_tags(),
            "cameras": snapshot.get("cameras", []),
            "camera_layout": self._identity_manager.layout_status(),
            "last_error": snapshot["last_error"],
        }

    def submit_ble_observation(
        self,
        *,
        uuid: str,
        major: int,
        minor: int,
        rssi: int,
        measured_power: int | None = None,
        receiver_id: str = "receiver-1",
    ) -> dict:
        if receiver_id not in RECEIVER_IDS:
            raise ValueError("지원하지 않는 BLE 수신기입니다.")
        uuid = uuid.replace("-", "").upper()
        tag_key = f"{uuid}:{major}:{minor}"
        now_monotonic = time.monotonic()
        with self._lock:
            previous = self._ble_tags.get(tag_key, {})
            observations = dict(previous.get("observations", {}))
            observations[receiver_id] = {
                "receiver_id": receiver_id,
                "rssi": rssi,
                "measured_power": measured_power,
                "last_seen": kst_now().isoformat(),
                "last_seen_monotonic": now_monotonic,
            }
            tag = {
                "tag_key": tag_key,
                "uuid": uuid,
                "major": major,
                "minor": minor,
                "observations": observations,
                "last_seen_monotonic": now_monotonic,
                "bound_track_id": previous.get("bound_track_id"),
            }
            self._ble_tags[tag_key] = tag
            self._update_receiver_strengths_locked(now_monotonic)
            return self._serialize_ble_tag(tag, now_monotonic)

    @staticmethod
    def _serialize_ble_tag(tag: dict, now: float) -> dict:
        receiver_rows = []
        for observation in tag.get("observations", {}).values():
            age_seconds = max(
                0.0,
                now - float(observation["last_seen_monotonic"]),
            )
            receiver_rows.append({
                key: value
                for key, value in observation.items()
                if key != "last_seen_monotonic"
            } | {
                "age_seconds": round(age_seconds, 1),
                "active": age_seconds <= BLE_TAG_ACTIVE_SECONDS,
            })
        receiver_rows.sort(key=lambda row: row["receiver_id"])
        active_rows = [row for row in receiver_rows if row["active"]]
        strongest = max(active_rows or receiver_rows, key=lambda row: row["rssi"], default=None)
        latest_seen = max(
            (row["last_seen"] for row in receiver_rows),
            default=None,
        )
        return {
            "tag_key": tag["tag_key"],
            "uuid": tag["uuid"],
            "major": tag["major"],
            "minor": tag["minor"],
            "rssi": strongest["rssi"] if strongest else None,
            "measured_power": strongest["measured_power"] if strongest else None,
            "last_seen": latest_seen,
            "bound_track_id": tag.get("bound_track_id"),
            "receivers": receiver_rows,
            "age_seconds": min(
                (row["age_seconds"] for row in receiver_rows),
                default=999.0,
            ),
            "active": bool(active_rows),
        }

    def _update_receiver_strengths_locked(self, now: float) -> None:
        strengths = {}
        for tag_key, tag in self._ble_tags.items():
            active_receivers = {
                receiver_id: int(observation["rssi"])
                for receiver_id, observation in tag.get("observations", {}).items()
                if now - float(observation["last_seen_monotonic"])
                <= BLE_TAG_ACTIVE_SECONDS
            }
            if active_receivers:
                strengths[tag_key] = active_receivers
        self._identity_manager.update_receiver_strengths(strengths)

    def get_ble_tags(self) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            self._ble_tags = {
                key: tag
                for key, tag in self._ble_tags.items()
                if tag.get("bound_track_id")
                or now - float(tag["last_seen_monotonic"]) <= 60.0
            }
            self._update_receiver_strengths_locked(now)
            tags = [self._serialize_ble_tag(tag, now) for tag in self._ble_tags.values()]
        return sorted(tags, key=lambda tag: (tag["major"], tag["minor"], tag["uuid"]))

    def active_ble_tag_keys(self) -> set[str]:
        return {tag["tag_key"] for tag in self.get_ble_tags() if tag["active"]}

    def _automatic_merge_candidates(self, track_id: str) -> list[str]:
        """Auto-merge only when every connected camera sees exactly one worker."""
        with self._lock:
            connected_states = [
                state
                for state in self._camera_states.values()
                if state["connected"]
            ]
            if (
                len(connected_states) < 2
                or any(len(state["workers"]) != 1 for state in connected_states)
            ):
                return []
            visible_ids = {
                state["workers"][0]["track_id"]
                for state in connected_states
            }
        if track_id not in visible_ids:
            return []
        return sorted(visible_ids - {track_id})

    def _rewrite_merged_workers(
        self,
        primary_track_id: str,
        merged_track_ids: list[str],
    ) -> None:
        merged = set(merged_track_ids)
        if not merged:
            return
        with self._lock:
            for tag in self._ble_tags.values():
                if tag.get("bound_track_id") in merged:
                    tag["bound_track_id"] = primary_track_id
            bound_tag_key = next(
                (
                    tag_key
                    for tag_key, tag in self._ble_tags.items()
                    if tag.get("bound_track_id") == primary_track_id
                ),
                None,
            )
            for state in self._camera_states.values():
                for worker in state["workers"]:
                    if worker.get("track_id") in merged:
                        worker["track_id"] = primary_track_id
                        worker["id"] = primary_track_id
                    if worker.get("track_id") == primary_track_id:
                        worker["ble_tag_key"] = bound_tag_key
        if self.external:
            self._refresh_external_status()

    def merge_person_ids(
        self,
        primary_track_id: str,
        duplicate_track_id: str,
    ) -> dict:
        if not self.external:
            raise ValueError("카메라 분석 중에만 작업자 ID를 병합할 수 있습니다.")
        with self._lock:
            visible_ids = {
                worker.get("track_id")
                for worker in self._status["workers"]
            }
        if primary_track_id not in visible_ids or duplicate_track_id not in visible_ids:
            raise ValueError("현재 화면에 보이는 두 작업자 ID를 선택하세요.")
        if primary_track_id == duplicate_track_id:
            raise ValueError("서로 다른 두 ID를 선택하세요.")

        merged = self._identity_manager.merge_track_ids(
            primary_track_id,
            [duplicate_track_id],
        )
        if not merged:
            raise ValueError("병합할 작업자 ID를 찾지 못했습니다.")
        self._rewrite_merged_workers(primary_track_id, merged)
        return {
            "ok": True,
            "primary_track_id": primary_track_id,
            "merged_track_ids": merged,
        }

    def bind_ble_tag(self, tag_key: str, track_id: str) -> dict:
        now = time.monotonic()
        with self._lock:
            tag = self._ble_tags.get(tag_key)
            if tag is None:
                raise ValueError("검색된 BLE 태그가 아닙니다.")
            if now - float(tag["last_seen_monotonic"]) > BLE_TAG_ACTIVE_SECONDS:
                raise ValueError("BLE 태그 신호가 만료되었습니다. 송신 상태를 확인하세요.")
            visible = any(
                worker.get("track_id") == track_id
                for worker in self._status["workers"]
            )
            if not visible:
                raise ValueError("현재 화면에서 확인되는 작업자 ID를 선택하세요.")

        merged_track_ids = []
        if self.external:
            candidates = self._automatic_merge_candidates(track_id)
            if candidates:
                try:
                    merged_track_ids = self._identity_manager.merge_track_ids(
                        track_id,
                        candidates,
                    )
                except ValueError:
                    # Distinct already-bound tags are never auto-merged.
                    merged_track_ids = []
            bound = self._identity_manager.bind_tag(tag_key, track_id)
        else:
            bound = self.person_tracker.bind_tag(tag_key, track_id)
        if not bound:
            raise ValueError("추적 ID가 더 이상 활성 상태가 아닙니다.")

        with self._lock:
            for key, item in self._ble_tags.items():
                if (
                    item.get("bound_track_id") == track_id
                    or item.get("bound_track_id") in merged_track_ids
                    or key == tag_key
                ):
                    item["bound_track_id"] = None
            self._ble_tags[tag_key]["bound_track_id"] = track_id
            response = self._serialize_ble_tag(self._ble_tags[tag_key], now)
            response["merged_track_ids"] = merged_track_ids
        self._rewrite_merged_workers(track_id, merged_track_ids)
        return response

    def unbind_ble_tag(self, tag_key: str) -> None:
        self._identity_manager.unbind_tag(tag_key)
        self.person_tracker.unbind_tag(tag_key)
        with self._lock:
            if tag_key in self._ble_tags:
                self._ble_tags[tag_key]["bound_track_id"] = None

    def reset_camera_layout(self) -> dict:
        self._identity_manager.reset_layout()
        return self._identity_manager.layout_status()

    def _set_status(self, **values):
        with self._lock:
            self._status.update(values)

    def _load_zones(self):
        zones = []
        with SessionLocal() as db:
            for zone in db.scalars(
                select(Zone).where(
                    Zone.site_id == self.site_id,
                    Zone.zone_type.in_(("no_entry", "fall_risk", "heavy_equip", "work_area")),
                )
            ).all():
                polygon = json.loads(zone.polygon)
                zones.append({
                    "id": zone.id,
                    "name": zone.name,
                    "zone_type": zone.zone_type,
                    "risk_level": zone.risk_level,
                    "visible": zone.visible,
                    "polygon": polygon,
                    "poly": Polygon(polygon),
                })
        self._zones = zones
        return zones

    def _save_events(self, events):
        now = time.monotonic()
        new_events = []

        with self._lock:
            if now - self._last_event_cleanup >= 60.0:
                cutoff = now - max(60.0, EVENT_COOLDOWN_SECONDS * 2.0)
                self._event_last_seen = {
                    key: seen_at
                    for key, seen_at in self._event_last_seen.items()
                    if seen_at >= cutoff
                }
                self._last_event_cleanup = now

            for event in events:
                if not should_persist_event(event):
                    continue
                # 같은 위험이라도 작업자가 다르면 별도 사건으로 기록한다.
                key = (event["type"], event.get("zone_id"), event.get("track_id"))
                last_seen = self._event_last_seen.get(key, 0.0)
                if now - last_seen < EVENT_COOLDOWN_SECONDS:
                    continue
                self._event_last_seen[key] = now
                new_events.append(Event(
                    site_id=self.site_id,
                    event_type=event["type"],
                    zone_id=event.get("zone_id"),
                    track_id=event.get("track_id"),
                    confidence=event.get("confidence", 0.0),
                ))

        if not new_events:
            return

        with SessionLocal() as db:
            db.add_all(new_events)
            db.commit()

    def _run(self, source: Path):
        cap = None
        try:
            from .detector import detector
            from .pipeline import process_frame

            zones = []
            next_zone_refresh = 0.0
            cap = cv2.VideoCapture(str(source))
            if not cap.isOpened():
                raise RuntimeError(f"영상을 열 수 없습니다: {source}")

            source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            frame_interval = 1.0 / source_fps
            next_frame_at = time.monotonic()
            rate_started_at = time.monotonic()
            rate_frames = 0
            processing_fps = 0.0
            detections = []
            pose_detections = []
            frame_index = 0

            self._set_status(
                stage="running",
                message="실시간 영상 분석 중",
                running=True,
            )

            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_zone_refresh:
                    zones = self._load_zones()
                    next_zone_refresh = now + ZONE_REFRESH_SECONDS
                ok, frame = cap.read()
                if not ok:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_index = 0
                    continue

                detections_fresh = frame_index % INFER_EVERY == 0
                if detections_fresh:
                    detections = detector.detect(frame)
                if frame_index % POSE_INFER_EVERY == 0:
                    pose_detections = pose_detector.detect(frame)

                original_encoded, original_jpeg = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 82],
                )
                height, width = frame.shape[:2]
                heat_status = (
                    self._heat_service.get_status()
                    if self._heat_service is not None
                    else None
                )
                self.person_tracker.set_active_ble_tags(self.active_ble_tag_keys())
                rendered, events, frame_status = process_frame(
                    frame,
                    detections,
                    zones,
                    width,
                    height,
                    self.person_tracker,
                    include_status=True,
                    is_outdoor=self.is_outdoor,
                    heat_status=heat_status,
                    heat_exposure_tracker=self._heat_exposure_tracker,
                    pose_detections=pose_detections,
                    detections_fresh=detections_fresh,
                )

                encoded, jpeg = cv2.imencode(
                    ".jpg",
                    rendered,
                    [cv2.IMWRITE_JPEG_QUALITY, 82],
                )
                if encoded:
                    with self._lock:
                        self._latest_jpeg = jpeg.tobytes()
                        if original_encoded:
                            self._latest_original_jpeg = original_jpeg.tobytes()
                        self._frame_version += 1

                self._save_events(events)
                # Episode 단위 집계 (site_id 주입)
                for ev in events:
                    ev.setdefault("site_id", self.site_id)
                self._episode_aggregator.process_events(events)
                self._exposure_accumulator.tick(
                    worker_count=frame_status.get("worker_count", 0),
                    frame_dt=frame_interval,
                )

                rate_frames += 1
                rate_elapsed = time.monotonic() - rate_started_at
                if rate_elapsed >= 1.0:
                    processing_fps = rate_frames / rate_elapsed
                    rate_frames = 0
                    rate_started_at = time.monotonic()

                self._set_status(
                    frame_index=frame_index,
                    processing_fps=round(processing_fps, 1),
                    **frame_status,
                )
                frame_index += 1

                next_frame_at += frame_interval
                delay = next_frame_at - time.monotonic()
                if delay > 0:
                    self._stop_event.wait(delay)
                else:
                    next_frame_at = time.monotonic()

        except Exception as exc:
            traceback.print_exc()
            logger.error("분석 스레드 오류: %s", exc)
            self._set_status(
                running=False,
                stage="error",
                message="영상 분석 중 오류가 발생했습니다.",
                last_error=str(exc),
            )
        finally:
            if cap is not None:
                cap.release()

    def _refresh_external_status(self, **overrides) -> dict:
        with self._lock:
            camera_rows = []
            workers_by_id: dict[str, dict] = {}
            total_fps = 0.0
            latest_frame_index = 0
            connected_count = 0
            has_frame = False
            last_error = None

            for camera_id in CAMERA_IDS:
                state = self._camera_states[camera_id]
                if state["connected"]:
                    connected_count += 1
                if state["latest_jpeg"] is not None:
                    has_frame = True
                total_fps += float(state["processing_fps"])
                latest_frame_index = max(latest_frame_index, int(state["frame_index"]))
                last_error = last_error or state.get("last_error")
                camera_rows.append({
                    "camera_id": camera_id,
                    "connected": bool(state["connected"]),
                    "frame_ready": state["latest_jpeg"] is not None,
                    "frame_index": int(state["frame_index"]),
                    "processing_fps": round(float(state["processing_fps"]), 1),
                    "worker_count": len(state["workers"]),
                    "last_error": state.get("last_error"),
                })
                for worker in state["workers"]:
                    track_id = worker["track_id"]
                    existing = workers_by_id.get(track_id)
                    if existing is None:
                        workers_by_id[track_id] = dict(worker)
                        continue
                    camera_ids = set(existing.get("camera_ids", []))
                    camera_ids.update(worker.get("camera_ids", []))
                    existing["camera_ids"] = sorted(camera_ids)
                    existing["helmet_violation"] = (
                        existing.get("helmet_violation", False)
                        or worker.get("helmet_violation", False)
                    )
                    existing["reasons"] = sorted(set(
                        existing.get("reasons", []) + worker.get("reasons", [])
                    ))
                    if worker.get("confidence", 0) > existing.get("confidence", 0):
                        preserved_cameras = existing["camera_ids"]
                        existing.update(worker)
                        existing["camera_ids"] = preserved_cameras

            workers = sorted(workers_by_id.values(), key=lambda row: row["track_id"])
            values = {
                "running": connected_count > 0,
                "stage": (
                    "running" if has_frame
                    else "waiting_frame" if connected_count
                    else "camera_disconnected"
                ),
                "message": (
                    f"{connected_count}대 카메라 실시간 분석 중"
                    if has_frame
                    else "카메라의 첫 프레임을 기다리고 있습니다."
                    if connected_count
                    else "카메라를 설정하거나 녹화된 영상을 업로드해 주세요."
                ),
                "source": "browser",
                "frame_index": latest_frame_index,
                "processing_fps": round(total_fps, 1),
                "workers": workers,
                "worker_count": len(workers),
                "unique_person_count": len(workers),
                "no_helmet_count": sum(
                    bool(worker.get("helmet_violation")) for worker in workers
                ),
                "cameras": camera_rows,
                "last_error": last_error,
            }
            values.update(overrides)
            self._status.update(values)
            return dict(values)

    def _run_external(self):
        try:
            from .detector import detector
            from .pipeline import process_frame

            zones = []
            next_zone_refresh = 0.0

            while not self._stop_event.is_set():
                if not self._frame_ready.wait(timeout=1.0):
                    continue
                self._frame_ready.clear()
                if self._stop_event.is_set():
                    break

                with self._lock:
                    both_connected = all(
                        state["connected"] for state in self._camera_states.values()
                    )
                if both_connected:
                    time.sleep(FRAME_BATCH_COALESCE_SECONDS)

                now = time.monotonic()
                if now >= next_zone_refresh:
                    zones = self._load_zones()
                    next_zone_refresh = now + ZONE_REFRESH_SECONDS

                jobs = []
                with self._lock:
                    for camera_id in CAMERA_IDS:
                        state = self._camera_states[camera_id]
                        jpeg_bytes = state["pending_jpeg"]
                        state["pending_jpeg"] = None
                        if state["connected"] and jpeg_bytes:
                            jobs.append((camera_id, jpeg_bytes))

                if not jobs:
                    continue

                frame_jobs = []
                for camera_id, jpeg_bytes in jobs:
                    array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                    frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    state = self._camera_states[camera_id]
                    frame_index = int(state["frame_index"])
                    frame_jobs.append({
                        "camera_id": camera_id,
                        "jpeg_bytes": jpeg_bytes,
                        "frame": frame,
                        "state": state,
                        "frame_index": frame_index,
                        "detections_fresh": (
                            frame_index % max(1, LIVE_INFER_EVERY) == 0
                        ),
                    })

                if not frame_jobs:
                    continue

                fresh_jobs = [job for job in frame_jobs if job["detections_fresh"]]
                if fresh_jobs:
                    detection_batches = detector.detect_batch([
                        job["frame"] for job in fresh_jobs
                    ])
                    for job, detections in zip(fresh_jobs, detection_batches):
                        job["state"]["detections"] = detections

                pose_due_jobs = [
                    job for job in frame_jobs
                    if job["frame_index"] % max(1, LIVE_POSE_INFER_EVERY) == 0
                ]
                for job in pose_due_jobs:
                    job["state"]["pose_detections"] = []
                pose_jobs = [
                    job for job in pose_due_jobs
                    if any(
                        detection["cls"] == "person"
                        for detection in job["state"]["detections"]
                    )
                ]
                if pose_jobs:
                    pose_batches = pose_detector.detect_batch([
                        job["frame"] for job in pose_jobs
                    ])
                    for job, poses in zip(pose_jobs, pose_batches):
                        job["state"]["pose_detections"] = poses

                active_ble_tags = self.active_ble_tag_keys()
                heat_status = (
                    self._heat_service.get_status()
                    if self._heat_service is not None
                    else None
                )
                for job in frame_jobs:
                    camera_id = job["camera_id"]
                    jpeg_bytes = job["jpeg_bytes"]
                    frame = job["frame"]
                    state = job["state"]
                    frame_index = job["frame_index"]
                    detections_fresh = job["detections_fresh"]
                    height, width = frame.shape[:2]
                    tracker = state["tracker"]
                    tracker.set_active_ble_tags(active_ble_tags)
                    rendered, events, frame_status = process_frame(
                        frame,
                        state["detections"],
                        zones,
                        width,
                        height,
                        tracker,
                        include_status=True,
                        is_outdoor=self.is_outdoor,
                        heat_status=heat_status,
                        heat_exposure_tracker=self._heat_exposure_tracker,
                        pose_detections=state["pose_detections"],
                        detections_fresh=detections_fresh,
                        identity_resolver=(
                            lambda tracks, timestamp, selected=camera_id:
                            self._identity_manager.resolve(
                                selected,
                                tracks,
                                timestamp,
                            )
                        ),
                    )
                    for worker in frame_status["workers"]:
                        worker["camera_id"] = camera_id
                        worker["camera_ids"] = [camera_id]

                    encoded, output_jpeg = cv2.imencode(
                        ".jpg",
                        rendered,
                        [cv2.IMWRITE_JPEG_QUALITY, 82],
                    )
                    completed_at = time.monotonic()
                    with self._lock:
                        if encoded:
                            state["latest_jpeg"] = output_jpeg.tobytes()
                            state["latest_original_jpeg"] = jpeg_bytes
                            state["latest_scene"] = frame.copy()
                            state["frame_version"] += 1
                        state["workers"] = frame_status["workers"]
                        state["frame_index"] = frame_index + 1
                        state["rate_frames"] += 1
                        elapsed = completed_at - float(state["rate_started_at"])
                        if elapsed >= 1.0:
                            state["processing_fps"] = state["rate_frames"] / elapsed
                            state["rate_frames"] = 0
                            state["rate_started_at"] = completed_at
                        state["last_error"] = None

                    self._save_events(events)
                    for event in events:
                        event.setdefault("site_id", self.site_id)
                    self._episode_aggregator.process_events(events)

                with self._lock:
                    left_scene = self._camera_states["camera-1"]["latest_scene"]
                    right_scene = self._camera_states["camera-2"]["latest_scene"]
                    both_connected = all(
                        state["connected"] for state in self._camera_states.values()
                    )
                if both_connected and left_scene is not None and right_scene is not None:
                    self._identity_manager.observe_visual_pair(
                        left_scene,
                        right_scene,
                    )

                status = self._refresh_external_status()
                self._exposure_accumulator.tick(
                    worker_count=status["worker_count"],
                )

        except Exception as exc:
            traceback.print_exc()
            logger.error("외부 카메라 분석 스레드 오류: %s", exc)
            self._set_status(
                running=False,
                stage="error",
                message="클라이언트 영상 분석 중 오류가 발생했습니다.",
                last_error=str(exc),
            )


class AnalysisRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._services: dict[int, AnalysisService] = {}

    def get(
        self,
        site_id: int,
        source: str | None = None,
        is_outdoor: bool = False,
        heat_service=None,
    ) -> AnalysisService:
        external = source == "browser"
        with self._lock:
            service = self._services.get(site_id)
            if service is None:
                service = AnalysisService(
                    site_id,
                    external=external,
                    is_outdoor=is_outdoor,
                    heat_service=heat_service,
                )
                self._services[site_id] = service
            elif heat_service is not None and service._heat_service is None:
                service._heat_service = heat_service
        if external:
            service.external = True
            service.start_external()
        return service

    def current(self, site_id: int) -> "AnalysisService | None":
        with self._lock:
            return self._services.get(site_id)

    def stop_site(self, site_id: int):
        with self._lock:
            service = self._services.pop(site_id, None)
        if service:
            service.stop()

    def stop_all(self):
        with self._lock:
            services = list(self._services.values())
            self._services.clear()
        for service in services:
            service.stop()


analysis_registry = AnalysisRegistry()
