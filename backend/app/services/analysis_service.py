"""영상 분석을 FastAPI 프로세스 안에서 실행하고 최신 결과를 공유한다."""

import json
import logging
import threading
import time
import traceback
from datetime import datetime, time as datetime_time
from pathlib import Path

logger = logging.getLogger(__name__)

import cv2
import numpy as np
from shapely.geometry import Polygon
from sqlalchemy import func, select

from ..config import (
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
from ..time_utils import kst_now, kst_today
from .episode_aggregator import EpisodeAggregator, ExposureAccumulator
from .person_tracking import CameraPersonTracker, HeatExposureTracker
from .pose_detector import pose_detector

INFER_EVERY = 3
EVENT_COOLDOWN_SECONDS = 10
ZONE_REFRESH_SECONDS = 1
INPUT_REQUIRED_MESSAGE = "카메라를 설정하거나 녹화된 영상을 업로드해 주세요."


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
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_jpeg: bytes | None = None
        self._latest_original_jpeg: bytes | None = None
        self._pending_jpeg: bytes | None = None
        self._external_connected = False
        self._frame_version = 0
        self._event_last_seen: dict[tuple[str, int | None], float] = {}
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

    def attach_external_camera(self) -> bool:
        self.start_external()
        with self._lock:
            if self._external_connected:
                return False
            self._external_connected = True
            self._latest_jpeg = None
            self._latest_original_jpeg = None
            self._status.update({
                "worker_count": 0,
                "no_helmet_count": 0,
                "workers": [],
            })
        self._set_status(
            running=True,
            stage="waiting_frame",
            message="카메라 첫 프레임을 기다리고 있습니다.",
            last_error=None,
        )
        return True

    def detach_external_camera(self):
        from .pose_behavior_detector import pose_behavior_detector

        self.person_tracker.flush()
        pose_behavior_detector.reset()
        self._episode_aggregator.flush()
        self._exposure_accumulator.flush()
        with self._lock:
            self._external_connected = False
            self._pending_jpeg = None
            self._latest_jpeg = None
            self._latest_original_jpeg = None
        self._set_status(
            running=False,
            stage="camera_disconnected",
            message="클라이언트 카메라 연결이 종료되었습니다.",
        )

    def submit_jpeg(self, jpeg: bytes):
        with self._lock:
            self._pending_jpeg = jpeg
        self._frame_ready.set()

    def stop(self):
        from .pose_behavior_detector import pose_behavior_detector

        self._stop_event.set()
        self._frame_ready.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self.person_tracker.flush()
        pose_behavior_detector.reset()
        self._episode_aggregator.flush()
        self._exposure_accumulator.flush()
        self._set_status(
            running=False,
            stage="stopped",
            message=INPUT_REQUIRED_MESSAGE,
        )

    def get_frame(self):
        with self._lock:
            return self._latest_jpeg, self._frame_version

    def get_original_frame(self):
        with self._lock:
            return self._latest_original_jpeg, self._frame_version

    def get_status(self):
        with self._lock:
            snapshot = dict(self._status)
            snapshot["workers"] = [dict(worker) for worker in self._status["workers"]]
        return snapshot

    def get_summary(self):
        snapshot = self.get_status()
        start_of_day = datetime.combine(kst_today(), datetime_time.min)
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
            "last_error": snapshot["last_error"],
        }

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

                if frame_index % INFER_EVERY == 0:
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

    def _run_external(self):
        try:
            from .detector import detector
            from .pipeline import process_frame

            zones = []
            next_zone_refresh = 0.0
            frame_index = 0
            rate_started_at = time.monotonic()
            rate_frames = 0
            processing_fps = 0.0
            detections = []
            pose_detections = []

            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_zone_refresh:
                    zones = self._load_zones()
                    next_zone_refresh = now + ZONE_REFRESH_SECONDS
                if not self._frame_ready.wait(timeout=1.0):
                    continue
                self._frame_ready.clear()
                if self._stop_event.is_set():
                    break

                with self._lock:
                    jpeg_bytes = self._pending_jpeg
                    self._pending_jpeg = None
                    connected = self._external_connected
                if not jpeg_bytes or not connected:
                    continue

                array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                original_jpeg = jpeg_bytes

                if frame_index % max(1, LIVE_INFER_EVERY) == 0:
                    detections = detector.detect(frame)
                if frame_index % max(1, LIVE_POSE_INFER_EVERY) == 0:
                    pose_detections = pose_detector.detect(frame)

                height, width = frame.shape[:2]
                heat_status = (
                    self._heat_service.get_status()
                    if self._heat_service is not None
                    else None
                )
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
                )

                encoded, output_jpeg = cv2.imencode(
                    ".jpg",
                    rendered,
                    [cv2.IMWRITE_JPEG_QUALITY, 82],
                )
                if encoded:
                    with self._lock:
                        self._latest_jpeg = output_jpeg.tobytes()
                        self._latest_original_jpeg = original_jpeg
                        self._frame_version += 1

                self._save_events(events)
                for ev in events:
                    ev.setdefault("site_id", self.site_id)
                self._episode_aggregator.process_events(events)
                self._exposure_accumulator.tick(
                    worker_count=frame_status.get("worker_count", 0),
                )

                rate_frames += 1
                elapsed = time.monotonic() - rate_started_at
                if elapsed >= 1.0:
                    processing_fps = rate_frames / elapsed
                    rate_frames = 0
                    rate_started_at = time.monotonic()

                self._set_status(
                    running=True,
                    stage="running",
                    message="클라이언트 카메라 실시간 분석 중",
                    frame_index=frame_index,
                    processing_fps=round(processing_fps, 1),
                    **frame_status,
                )
                frame_index += 1

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
