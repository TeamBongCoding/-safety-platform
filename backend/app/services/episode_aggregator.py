"""EpisodeAggregator — 연속 프레임의 동일 위험을 하나의 Episode로 집계한다.

설계 원칙
---------
- FastAPI/OpenCV에 결합하지 않는다. 단위 테스트에서 시간과 DB를 mock 가능.
- 프레임마다 DB commit하지 않는다. UPDATE_INTERVAL_SEC 마다 열린 episode를 갱신한다.
- close_gap_sec 동안 같은 키의 이벤트가 없으면 episode를 닫는다.
- min_duration_sec 미만의 episode는 DB에 저장하지 않는다.
- stagger 등 should_persist=False 이벤트는 건너뛴다.
- 다른 track_id는 별도 episode.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, NamedTuple
from ..time_utils import utc_now

logger = logging.getLogger(__name__)


class EpisodeKey(NamedTuple):
    site_id: int | None
    camera_id: str | None
    event_type: str
    zone_id: int | None
    track_id: str | None


@dataclass
class OpenEpisode:
    key: EpisodeKey
    started_at: float          # monotonic
    started_dt: datetime
    last_seen: float           # monotonic
    confidence_min: float = 0.0
    confidence_max: float = 0.0
    confidence_sum: float = 0.0
    observation_count: int = 0
    last_db_update: float = 0.0
    db_id: int | None = None   # None until first INSERT


def _severity_from_event_type(event_type: str) -> str:
    high = {"fall", "fall_still", "heat_fall", "heat_fall_still", "zone_intrusion"}
    medium = {"no_helmet", "fall_risk_entry", "heavy_equipment_entry", "sudden_sit",
              "heat_sudden_sit", "heat_stagger"}
    if event_type in high:
        return "high"
    if event_type in medium:
        return "medium"
    return "low"


class EpisodeAggregator:
    """Accumulates raw detection events into persisted EventEpisode rows.

    Parameters
    ----------
    session_factory:
        Callable that returns a SQLAlchemy Session (context manager).
    should_persist:
        Callable(event_dict) → bool — same policy as analysis_service.should_persist_event.
    close_gap_sec:
        Seconds without observation before closing an episode.
    min_duration_sec:
        Episodes shorter than this are discarded (flash detections).
    update_interval_sec:
        How often open episodes are flushed to DB while they are still running.
    model_version / rule_version:
        Stamped on every persisted episode for auditability.
    now_fn:
        Injectable clock for unit tests.
    """

    def __init__(
        self,
        session_factory: Callable,
        should_persist: Callable[[dict], bool],
        close_gap_sec: float = 30.0,
        min_duration_sec: float = 2.0,
        update_interval_sec: float = 10.0,
        model_version: str = "1.0",
        rule_version: str = "1.0",
        now_fn: Callable[[], float] | None = None,
    ):
        self._session_factory = session_factory
        self._should_persist = should_persist
        self._close_gap = close_gap_sec
        self._min_duration = min_duration_sec
        self._update_interval = update_interval_sec
        self._model_version = model_version
        self._rule_version = rule_version
        self._now = now_fn or time.monotonic
        self._open: dict[EpisodeKey, OpenEpisode] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def process_events(self, events: list[dict]) -> None:
        """Call once per processed frame with the list of raw events."""
        now = self._now()

        # Extend or open episodes for current events
        for event in events:
            if not self._should_persist(event):
                continue
            key = EpisodeKey(
                site_id=event.get("site_id"),
                camera_id=event.get("camera_id"),
                event_type=event["type"],
                zone_id=event.get("zone_id"),
                track_id=event.get("track_id"),
            )
            conf = float(event.get("confidence", 0.0))
            if key in self._open:
                ep = self._open[key]
                ep.last_seen = now
                ep.confidence_min = min(ep.confidence_min, conf)
                ep.confidence_max = max(ep.confidence_max, conf)
                ep.confidence_sum += conf
                ep.observation_count += 1
                if now - ep.last_db_update >= self._update_interval:
                    self._upsert_episode(ep, now, close=False)
                    ep.last_db_update = now
            else:
                ep = OpenEpisode(
                    key=key,
                    started_at=now,
                    started_dt=utc_now(),
                    last_seen=now,
                    confidence_min=conf,
                    confidence_max=conf,
                    confidence_sum=conf,
                    observation_count=1,
                    last_db_update=now,
                )
                self._open[key] = ep
                self._upsert_episode(ep, now, close=False)

        # Close stale episodes (gap exceeded)
        stale = [k for k, ep in self._open.items() if now - ep.last_seen >= self._close_gap]
        for key in stale:
            ep = self._open.pop(key)
            self._upsert_episode(ep, now, close=True)

    def flush(self) -> None:
        """Close all open episodes immediately (call on shutdown or stream end)."""
        now = self._now()
        for ep in list(self._open.values()):
            self._upsert_episode(ep, now, close=True)
        self._open.clear()

    def open_episode_count(self) -> int:
        return len(self._open)

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _upsert_episode(self, ep: OpenEpisode, now: float, close: bool) -> None:
        from ..models import EventEpisode

        # 실제 관측 기간 = 마지막 감지 - 처음 감지 (gap 대기 시간 제외)
        observation_duration = ep.last_seen - ep.started_at
        # 업데이트/DB 저장용 표시 기간 = 현재 시각 - 처음 감지
        duration = now - ep.started_at if not close else observation_duration
        if close and observation_duration < self._min_duration:
            # Discard flash detection — also remove from DB if it was already inserted
            if ep.db_id is not None:
                try:
                    with self._session_factory() as db:
                        row = db.get(EventEpisode, ep.db_id)
                        if row:
                            db.delete(row)
                            db.commit()
                except Exception as exc:
                    logger.warning("Failed to delete short episode %s: %s", ep.db_id, exc)
            return

        conf_min = ep.confidence_min
        conf_max = ep.confidence_max
        conf_avg = ep.confidence_sum / max(1, ep.observation_count)
        ended_at = utc_now() if close else None

        try:
            with self._session_factory() as db:
                if ep.db_id is None:
                    row = EventEpisode(
                        site_id=ep.key.site_id,
                        camera_id=ep.key.camera_id,
                        track_id=ep.key.track_id,
                        event_type=ep.key.event_type,
                        zone_id=ep.key.zone_id,
                        started_at=ep.started_dt,
                        ended_at=ended_at,
                        duration_sec=round(duration, 2),
                        severity=_severity_from_event_type(ep.key.event_type),
                        confidence_min=round(conf_min, 4),
                        confidence_avg=round(conf_avg, 4),
                        confidence_max=round(conf_max, 4),
                        observation_count=ep.observation_count,
                        model_version=self._model_version,
                        rule_version=self._rule_version,
                    )
                    db.add(row)
                    db.flush()
                    ep.db_id = row.id
                    db.commit()
                else:
                    row = db.get(EventEpisode, ep.db_id)
                    if row:
                        row.ended_at = ended_at
                        row.duration_sec = round(duration, 2)
                        row.confidence_min = round(conf_min, 4)
                        row.confidence_avg = round(conf_avg, 4)
                        row.confidence_max = round(conf_max, 4)
                        row.observation_count = ep.observation_count
                        row.updated_at = utc_now()
                        db.commit()
        except Exception as exc:
            logger.error("EpisodeAggregator DB error: %s", exc)


class ExposureAccumulator:
    """Per-frame worker exposure → hourly bucket flusher.

    Accumulates (observed_seconds, worker_seconds, …) and writes to
    ExposureHourly rows every flush_interval_sec.
    """

    def __init__(
        self,
        site_id: int | None,
        session_factory: Callable,
        camera_id: str | None = None,
        flush_interval_sec: float = 60.0,
        now_fn: Callable[[], float] | None = None,
    ):
        self._site_id = site_id
        self._camera_id = camera_id
        self._session_factory = session_factory
        self._flush_interval = flush_interval_sec
        self._now = now_fn or time.monotonic
        self._bucket_start: datetime | None = None
        self._obs_sec = 0.0
        self._worker_sec = 0.0
        self._max_workers = 0
        self._total_workers = 0
        self._frame_count = 0
        self._last_flush = self._now()
        self._last_tick = self._now()

    def tick(self, worker_count: int, frame_dt: float = 0.033) -> None:
        """Call once per processed frame.

        Parameters
        ----------
        worker_count: number of workers detected this frame.
        frame_dt: elapsed seconds since last frame (used to accumulate seconds).
        """
        from datetime import datetime
        now = self._now()
        if self._bucket_start is None:
            self._bucket_start = utc_now()

        self._obs_sec += frame_dt
        self._worker_sec += worker_count * frame_dt
        self._max_workers = max(self._max_workers, worker_count)
        self._total_workers += worker_count
        self._frame_count += 1

        if now - self._last_flush >= self._flush_interval:
            self._flush()
            self._last_flush = now

    def flush(self) -> None:
        if self._frame_count > 0:
            self._flush()

    def _flush(self) -> None:
        from ..models import ExposureHourly
        if self._frame_count == 0 or self._bucket_start is None:
            return
        avg_workers = self._total_workers / self._frame_count

        try:
            with self._session_factory() as db:
                row = ExposureHourly(
                    site_id=self._site_id,
                    camera_id=self._camera_id,
                    bucket_start=self._bucket_start,
                    observed_seconds=round(self._obs_sec, 2),
                    worker_seconds=round(self._worker_sec, 2),
                    max_concurrent_workers=self._max_workers,
                    average_workers=round(avg_workers, 3),
                    frame_count=self._frame_count,
                )
                db.add(row)
                db.commit()
        except Exception as exc:
            logger.error("ExposureAccumulator flush error: %s", exc)

        self._bucket_start = None
        self._obs_sec = 0.0
        self._worker_sec = 0.0
        self._max_workers = 0
        self._total_workers = 0
        self._frame_count = 0
