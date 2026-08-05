"""영상 분석을 FastAPI 프로세스 안에서 실행하고 최신 결과를 공유한다."""

import json
import threading
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path

import cv2
from shapely.geometry import Polygon
from sqlalchemy import func, select

from ..config import PROJECT_ROOT, VIDEO_SOURCE
from ..database import SessionLocal
from ..models import Event, Zone
from . import harness_store

DEFAULT_VIDEO_PATH = PROJECT_ROOT / "data" / "videos" / "site1.mp4"
INFER_EVERY = 3
EVENT_COOLDOWN_SECONDS = 10


class AnalysisService:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest_jpeg: bytes | None = None
        self._frame_version = 0
        self._event_last_seen: dict[tuple[str, int | None], float] = {}
        self._status = {
            "running": False,
            "stage": "stopped",
            "message": "분석이 시작되지 않았습니다.",
            "source": None,
            "frame_index": 0,
            "processing_fps": 0.0,
            "worker_count": 0,
            "no_helmet_count": 0,
            "unsecured_count": 0,
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

    def stop(self):
        self._stop_event.set()
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
        snapshot["harness"] = harness_store.get("worker-1")
        return snapshot

    def get_summary(self):
        snapshot = self.get_status()
        start_of_day = datetime.combine(date.today(), datetime_time.min)
        with SessionLocal() as db:
            violations_today = db.scalar(
                select(func.count(Event.id)).where(Event.timestamp >= start_of_day)
            ) or 0

        return {
            "type": "summary",
            "timestamp": datetime.now().isoformat(),
            "worker_count": snapshot["worker_count"],
            "no_helmet_count": snapshot["no_helmet_count"],
            "unsecured_count": snapshot["unsecured_count"],
            "violations_today": violations_today,
            "analysis_running": snapshot["running"],
            "analysis_stage": snapshot["stage"],
            "analysis_message": snapshot["message"],
            "frame_index": snapshot["frame_index"],
            "processing_fps": snapshot["processing_fps"],
            "workers": snapshot["workers"],
            "harness": snapshot["harness"],
            "last_error": snapshot["last_error"],
        }

    def _set_status(self, **values):
        with self._lock:
            self._status.update(values)

    def _load_zones(self):
        zones = []
        with SessionLocal() as db:
            for zone in db.scalars(select(Zone)).all():
                polygon = json.loads(zone.polygon)
                zones.append({
                    "id": zone.id,
                    "zone_type": zone.zone_type,
                    "polygon": polygon,
                    "poly": Polygon(polygon),
                })
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
                event_type=event["type"],
                zone_id=event.get("zone_id"),
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

            zones = self._load_zones()
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


analysis_service = AnalysisService()
