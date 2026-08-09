"""영상 분석을 FastAPI 프로세스 안에서 실행하고 최신 결과를 공유한다."""

import json
import threading
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Polygon
from sqlalchemy import func, select

from ..config import ANALYSIS_ENABLED, PROJECT_ROOT, VIDEO_SOURCE
from ..database import SessionLocal
from ..models import Event, Site, User, Zone
from .person_tracking import CameraPersonTracker, GlobalIdentityManager

DEFAULT_VIDEO_PATH = PROJECT_ROOT / "data" / "videos" / "site1.mp4"
INFER_EVERY = 3
EVENT_COOLDOWN_SECONDS = 10
ZONE_REFRESH_SECONDS = 1


class AnalysisService:
    def __init__(
        self,
        site_id: int,
        identity_manager: GlobalIdentityManager,
        camera_id: int | None = None,
        external: bool = False,
    ):
        self.site_id = site_id
        self.camera_id = camera_id
        self.external = external
        self.identity_manager = identity_manager
        self.person_tracker = CameraPersonTracker(camera_id, identity_manager)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_jpeg: bytes | None = None
        self._pending_jpeg: bytes | None = None
        self._external_connected = False
        self._frame_version = 0
        self._event_last_seen: dict[tuple[str, int | None], float] = {}
        self._zones: list[dict] = []
        self._status = {
            "running": False,
            "stage": "stopped",
            "message": "분석이 시작되지 않았습니다.",
            "source": None,
            "frame_index": 0,
            "processing_fps": 0.0,
            "worker_count": 0,
            "no_helmet_count": 0,
            "transition_candidate_count": 0,
            "entry_roi_count": 0,
            "exit_roi_count": 0,
            "reid_backend": None,
            "workers": [],
            "last_error": None,
        }

    def start(self, video_path: str | None = None):
        if self._thread and self._thread.is_alive():
            return

        source = Path(video_path or VIDEO_SOURCE or DEFAULT_VIDEO_PATH)
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
            name=f"browser-camera-analysis-{self.camera_id}",
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
            self._status.update({
                "worker_count": 0,
                "no_helmet_count": 0,
                "transition_candidate_count": 0,
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
        exit_zones = [zone for zone in self._zones if zone["zone_type"] == "camera_exit"]
        self.person_tracker.flush(exit_zones)
        with self._lock:
            self._external_connected = False
            self._pending_jpeg = None
            self._latest_jpeg = None
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
        self._stop_event.set()
        self._frame_ready.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._set_status(running=False, stage="stopped", message="분석이 중지되었습니다.")

    def get_frame(self):
        with self._lock:
            return self._latest_jpeg, self._frame_version

    def get_status(self):
        with self._lock:
            snapshot = dict(self._status)
            snapshot["workers"] = [dict(worker) for worker in self._status["workers"]]
        return snapshot

    def get_summary(self):
        snapshot = self.get_status()
        start_of_day = datetime.combine(date.today(), datetime_time.min)
        with SessionLocal() as db:
            violations_today = db.scalar(
                select(func.count(Event.id)).where(
                    Event.site_id == self.site_id,
                    Event.timestamp >= start_of_day,
                )
            ) or 0

        return {
            "type": "summary",
            "camera_id": self.camera_id,
            "source": snapshot["source"],
            "timestamp": datetime.now().isoformat(),
            "worker_count": snapshot["worker_count"],
            "no_helmet_count": snapshot["no_helmet_count"],
            "transition_candidate_count": snapshot["transition_candidate_count"],
            "entry_roi_count": snapshot["entry_roi_count"],
            "exit_roi_count": snapshot["exit_roi_count"],
            "reid_backend": snapshot["reid_backend"],
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
            camera_filter = (
                Zone.camera_id == self.camera_id
                if self.camera_id is not None
                else Zone.camera_id.is_(None)
            )
            for zone in db.scalars(
                select(Zone).where(Zone.site_id == self.site_id, camera_filter)
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
            key = (event["type"], event.get("zone_id"))
            last_seen = self._event_last_seen.get(key, 0.0)
            if now - last_seen < EVENT_COOLDOWN_SECONDS:
                continue
            self._event_last_seen[key] = now
            new_events.append(Event(
                site_id=self.site_id,
                camera_id=self.camera_id,
                event_type=event["type"],
                zone_id=event.get("zone_id"),
                confidence=event.get("confidence", 0.0),
            ))

        if not new_events:
            return

        with SessionLocal() as db:
            owner_role = db.scalar(
                select(User.role)
                .join(Site, Site.user_id == User.id)
                .where(Site.id == self.site_id)
            )
            if owner_role == "platform_admin":
                return
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

                height, width = frame.shape[:2]
                rendered, events, frame_status = process_frame(
                    frame,
                    detections,
                    zones,
                    width,
                    height,
                    self.person_tracker,
                    self.identity_manager,
                    include_status=True,
                )

                encoded, jpeg = cv2.imencode(
                    ".jpg",
                    rendered,
                    [cv2.IMWRITE_JPEG_QUALITY, 82],
                )
                if encoded:
                    with self._lock:
                        self._latest_jpeg = jpeg.tobytes()
                        self._frame_version += 1

                self._save_events(events)

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

                if frame_index % INFER_EVERY == 0:
                    detections = detector.detect(frame)
                height, width = frame.shape[:2]
                rendered, events, frame_status = process_frame(
                    frame,
                    detections,
                    zones,
                    width,
                    height,
                    self.person_tracker,
                    self.identity_manager,
                    include_status=True,
                )

                encoded, output_jpeg = cv2.imencode(
                    ".jpg",
                    rendered,
                    [cv2.IMWRITE_JPEG_QUALITY, 82],
                )
                if encoded:
                    with self._lock:
                        self._latest_jpeg = output_jpeg.tobytes()
                        self._frame_version += 1

                self._save_events(events)
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
            self._set_status(
                running=False,
                stage="error",
                message="클라이언트 영상 분석 중 오류가 발생했습니다.",
                last_error=str(exc),
            )


class AnalysisRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._services: dict[tuple[int, int | None], AnalysisService] = {}
        self._identity_managers: dict[int, GlobalIdentityManager] = {}

    def get(
        self,
        site_id: int,
        camera_id: int | None = None,
        source: str | None = None,
    ) -> AnalysisService:
        key = (site_id, camera_id)
        external = source == "browser"
        with self._lock:
            service = self._services.get(key)
            if service is None:
                identity_manager = self._identity_managers.setdefault(
                    site_id, GlobalIdentityManager()
                )
                service = AnalysisService(
                    site_id,
                    identity_manager,
                    camera_id=camera_id,
                    external=external,
                )
                self._services[key] = service
        if external:
            service.start_external()
        elif ANALYSIS_ENABLED:
            service.start(source)
        return service

    def stop_camera(self, site_id: int, camera_id: int | None):
        key = (site_id, camera_id)
        with self._lock:
            service = self._services.pop(key, None)
        if service:
            service.stop()

    def stop_all(self):
        with self._lock:
            services = list(self._services.values())
            self._services.clear()
        for service in services:
            service.stop()


analysis_registry = AnalysisRegistry()
