"""Camera-local person tracking and cross-camera identity hand-off."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np
from shapely.geometry import Point

from ..config import (
    EMBEDDING_HISTORY_SIZE,
    EMBEDDING_MIN_QUALITY,
    HELMET_VOTE_WINDOW_SECONDS,
    OVERLAP_TIME_TOLERANCE,
    REID_DEEP_WEIGHT,
    REID_ENTRY_GRACE_FRAMES,
    REID_MAX_TRANSITION_SECONDS,
    REID_MIN_SIMILARITY,
    REID_OVERLAP_THRESHOLD,
    REID_ROI_MARGIN,
    REID_SCORE_THRESHOLD,
    REID_STRONG_MATCH_THRESHOLD,
    TRACK_MAX_MISSED_FRAMES,
)

DEEP_EMBEDDING_SIZE = 2048


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-8:
        return 0.0
    return float(np.clip(np.dot(left, right) / denominator, 0.0, 1.0))


def _point_in_any(point: tuple[float, float], zones: list[dict]) -> dict | None:
    shaped = Point(point)
    return next((
        zone
        for zone in zones
        if zone["poly"].buffer(max(0.0, REID_ROI_MARGIN)).covers(shaped)
    ), None)


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(*vector)
    if length <= 1e-8:
        return (0.0, 0.0)
    return (vector[0] / length, vector[1] / length)


def _direction_score(
    direction: tuple[float, float],
    point: tuple[float, float],
    zone: dict,
    entering: bool,
) -> float:
    """Score motion toward an exit ROI or away from an entry ROI."""
    if direction == (0.0, 0.0):
        return 0.5
    center = (zone["poly"].centroid.x, zone["poly"].centroid.y)
    expected = (point[0] - center[0], point[1] - center[1]) if entering else (
        center[0] - point[0],
        center[1] - point[1],
    )
    expected = _unit(expected)
    if expected == (0.0, 0.0):
        return 0.5
    dot = direction[0] * expected[0] + direction[1] * expected[1]
    return float(np.clip((dot + 1.0) / 2.0, 0.0, 1.0))


def appearance_embedding(frame: np.ndarray, box: list[float]) -> tuple[np.ndarray, float]:
    """Build a normalized person appearance descriptor and crop quality score."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in box]
    x1, x2 = sorted((max(0, x1), min(width, x2)))
    y1, y2 = sorted((max(0, y1), min(height, y2)))
    if x2 - x1 < 4 or y2 - y1 < 8:
        return np.zeros(144, dtype=np.float32), 0.0

    crop = frame[y1:y2, x1:x2]
    resized = cv2.resize(crop, (64, 128), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
    features: list[np.ndarray] = []
    for stripe in np.array_split(hsv, 4, axis=0):
        for channel, limit in ((0, 180), (1, 256), (2, 256)):
            histogram = cv2.calcHist([stripe], [channel], None, [8], [0, limit]).ravel()
            features.append(histogram)
    for channel in range(3):
        features.append(cv2.calcHist([lab], [channel], None, [16], [0, 256]).ravel())

    embedding = np.concatenate(features).astype(np.float32)
    norm = float(np.linalg.norm(embedding))
    if norm > 1e-8:
        embedding /= norm

    crop_area = (x2 - x1) * (y2 - y1)
    area_score = min(1.0, crop_area / max(1.0, width * height * 0.08))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness_score = min(1.0, sharpness / 160.0)
    brightness_score = max(0.0, 1.0 - abs(float(gray.mean()) - 128.0) / 128.0)
    quality = 0.45 * area_score + 0.35 * sharpness_score + 0.20 * brightness_score
    return embedding, round(float(quality), 4)


def combined_appearance_embedding(
    color_embedding: np.ndarray,
    deep_embedding,
) -> np.ndarray:
    """Fuse camera-robust deep features with color details for Re-ID."""
    color = np.asarray(color_embedding, dtype=np.float32).reshape(-1)
    color_norm = float(np.linalg.norm(color))
    if color_norm > 1e-8:
        color = color / color_norm

    deep = np.zeros(DEEP_EMBEDDING_SIZE, dtype=np.float32)
    if deep_embedding is not None:
        raw = np.asarray(deep_embedding, dtype=np.float32).reshape(-1)
        copy_size = min(DEEP_EMBEDDING_SIZE, raw.size)
        deep[:copy_size] = raw[:copy_size]
        deep_norm = float(np.linalg.norm(deep))
        if deep_norm > 1e-8:
            deep /= deep_norm

    if np.linalg.norm(deep) <= 1e-8:
        fused = np.concatenate((deep, color))
    else:
        deep_weight = float(np.clip(REID_DEEP_WEIGHT, 0.0, 1.0))
        fused = np.concatenate((
            deep * math.sqrt(deep_weight),
            color * math.sqrt(1.0 - deep_weight),
        ))
    norm = float(np.linalg.norm(fused))
    return fused / norm if norm > 1e-8 else fused


def _iou(left: list[float], right: list[float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


class EmbeddingHistory:
    """최근 N개의 고품질 embedding을 저장하고 품질 가중 대표 embedding을 계산한다."""

    def __init__(self, maxsize: int = EMBEDDING_HISTORY_SIZE):
        self._buf: deque = deque(maxlen=maxsize)

    def add(self, embedding: np.ndarray, quality: float) -> None:
        if quality >= EMBEDDING_MIN_QUALITY:
            self._buf.append((embedding.copy(), quality))

    def representative(self) -> np.ndarray | None:
        if not self._buf:
            return None
        embs, quals = zip(*self._buf)
        weights = np.array(quals, dtype=np.float32)
        stacked = np.stack(embs)
        weighted = (stacked * weights[:, None]).sum(axis=0)
        norm = float(np.linalg.norm(weighted))
        return weighted / norm if norm > 1e-8 else weighted

    def __len__(self) -> int:
        return len(self._buf)


@dataclass
class TransitionCandidate:
    global_person_id: str
    source_camera_id: int | None
    exited_at: float
    embedding: np.ndarray
    quality: float
    direction_score: float


@dataclass
class ActiveTrackInfo:
    """다른 카메라에서 현재 관측 중인 트랙 정보 (중복 시야 매칭용)."""
    global_person_id: str
    embedding: np.ndarray
    quality: float
    timestamp: float
    in_overlap_zone: bool


class GlobalIdentityManager:
    """Site-scoped global identity state shared by every camera service."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_global_id = 1
        self._pending: list[TransitionCandidate] = []
        self._embeddings: dict[str, np.ndarray] = {}
        # 중복 시야 실시간 매칭용 활성 트랙 레지스트리
        self._active_tracks: dict[int, dict[int, ActiveTrackInfo]] = {}
        # 헬멧 크로스카메라 집계: global_id -> deque of (timestamp, camera_id, helmet_on, quality)
        self._helmet_observations: dict[str, deque] = {}
        # ID 병합 앨리어스: drop_id -> canonical_id
        self._id_aliases: dict[str, str] = {}
        # pipeline에서 drain해야 할 ID 병합 이벤트 (dropped, canonical)
        self._pending_merges: list[tuple[str, str]] = []

    def _new_id(self) -> str:
        identity = f"person-{self._next_global_id:06d}"
        self._next_global_id += 1
        return identity

    def _purge_expired(self, timestamp: float):
        self._pending = [
            item for item in self._pending
            if timestamp - item.exited_at <= REID_MAX_TRANSITION_SECONDS
        ]

    def _resolve_id_nolock(self, global_person_id: str) -> str:
        """앨리어스 체인을 따라 정규 Global ID를 반환한다 (lock 없이)."""
        visited: set[str] = set()
        current = global_person_id
        while current in self._id_aliases:
            next_id = self._id_aliases[current]
            if next_id in visited:
                break
            visited.add(current)
            current = next_id
        return current

    def resolve_id(self, global_person_id: str) -> str:
        """앨리어스 체인을 따라 정규 Global ID를 반환한다."""
        with self._lock:
            return self._resolve_id_nolock(global_person_id)

    def _merge_ids_nolock(self, keep_id: str, drop_id: str) -> None:
        """drop_id를 keep_id로 병합한다 (caller가 lock 보유해야 함)."""
        if keep_id == drop_id:
            return
        # 앨리어스 등록
        self._id_aliases[drop_id] = keep_id
        # 임베딩 병합
        keep_emb = self._embeddings.get(keep_id)
        drop_emb = self._embeddings.pop(drop_id, None)
        if drop_emb is not None:
            if keep_emb is not None:
                merged = keep_emb * 0.6 + drop_emb * 0.4
                norm = float(np.linalg.norm(merged))
                self._embeddings[keep_id] = merged / norm if norm > 1e-8 else merged
            else:
                self._embeddings[keep_id] = drop_emb
        # pending 전환 후보 업데이트
        for cand in self._pending:
            if cand.global_person_id == drop_id:
                cand.global_person_id = keep_id
        # active_tracks 업데이트
        for cam_tracks in self._active_tracks.values():
            for info in cam_tracks.values():
                if info.global_person_id == drop_id:
                    info.global_person_id = keep_id
        # 헬멧 관측 이전
        drop_obs = self._helmet_observations.pop(drop_id, None)
        if drop_obs:
            keep_obs = self._helmet_observations.setdefault(keep_id, deque(maxlen=50))
            keep_obs.extend(drop_obs)
        # pipeline이 HeatExposureTracker를 업데이트할 수 있도록 병합 이벤트 기록
        self._pending_merges.append((drop_id, keep_id))

    def drain_pending_merges(self) -> list[tuple[str, str]]:
        """pipeline이 HeatExposureTracker ID 병합에 사용할 이벤트 목록을 반환하고 초기화한다."""
        with self._lock:
            result, self._pending_merges = self._pending_merges, []
            return result

    def _match_pending(
        self,
        camera_id: int | None,
        embedding: np.ndarray,
        quality: float,
        point: tuple[float, float],
        direction: tuple[float, float],
        entry_zones: list[dict],
        timestamp: float,
    ) -> tuple[str, dict] | None:
        entry_zone = _point_in_any(point, entry_zones)
        if entry_zone is None:
            return None

        entry_direction = _direction_score(direction, point, entry_zone, entering=True)
        best: tuple[float, int, dict] | None = None
        for index, candidate in enumerate(self._pending):
            if candidate.source_camera_id == camera_id:
                continue
            elapsed = max(0.0, timestamp - candidate.exited_at)
            reid_score = _cosine_similarity(candidate.embedding, embedding)
            if reid_score < REID_MIN_SIMILARITY:
                continue
            time_score = max(0.0, 1.0 - elapsed / REID_MAX_TRANSITION_SECONDS)
            if direction == (0.0, 0.0):
                direction_score = candidate.direction_score
            else:
                direction_score = (candidate.direction_score + entry_direction) / 2.0
            quality_score = min(candidate.quality, quality)
            total = (
                0.65 * reid_score
                + 0.15 * time_score
                + 0.12 * direction_score
                + 0.08 * quality_score
            )
            details = {
                "matched_from_camera_id": candidate.source_camera_id,
                "transition_seconds": round(elapsed, 2),
                "reid_similarity": round(reid_score, 3),
                "direction_score": round(direction_score, 3),
                "quality_score": round(quality_score, 3),
                "match_score": round(total, 3),
            }
            if total >= REID_SCORE_THRESHOLD and (best is None or total > best[0]):
                best = (total, index, details)

        if best is None:
            return None
        _, index, details = best
        candidate = self._pending.pop(index)
        self._embeddings[candidate.global_person_id] = embedding.copy()
        return candidate.global_person_id, details

    def assign(
        self,
        camera_id: int | None,
        embedding: np.ndarray,
        quality: float,
        point: tuple[float, float],
        direction: tuple[float, float],
        entry_zones: list[dict],
        timestamp: float,
    ) -> tuple[str, dict | None]:
        with self._lock:
            self._purge_expired(timestamp)
            matched = self._match_pending(
                camera_id, embedding, quality, point, direction, entry_zones, timestamp,
            )
            if matched is not None:
                global_id, details = matched
                return self._resolve_id_nolock(global_id), details

            global_id = self._new_id()
            self._embeddings[global_id] = embedding.copy()
            return global_id, None

    def try_transition_match(
        self,
        camera_id: int | None,
        embedding: np.ndarray,
        quality: float,
        entry_point: tuple[float, float],
        direction: tuple[float, float],
        entry_zones: list[dict],
        timestamp: float,
    ) -> tuple[str, dict] | None:
        """Retry a hand-off while a newly entered track is still in its grace period."""
        with self._lock:
            self._purge_expired(timestamp)
            result = self._match_pending(
                camera_id, embedding, quality, entry_point, direction, entry_zones, timestamp,
            )
            if result is not None:
                global_id, details = result
                return self._resolve_id_nolock(global_id), details
            return None

    def update_embedding(self, global_person_id: str, embedding: np.ndarray, quality: float):
        if quality < EMBEDDING_MIN_QUALITY:
            return
        with self._lock:
            canonical = self._resolve_id_nolock(global_person_id)
            current = self._embeddings.get(canonical)
            mixed = embedding if current is None else current * 0.7 + embedding * 0.3
            norm = float(np.linalg.norm(mixed))
            self._embeddings[canonical] = mixed / norm if norm > 1e-8 else mixed

    def register_departure(
        self,
        global_person_id: str,
        camera_id: int | None,
        embedding: np.ndarray,
        quality: float,
        point: tuple[float, float],
        direction: tuple[float, float],
        exit_zones: list[dict],
        timestamp: float,
    ) -> bool:
        exit_zone = _point_in_any(point, exit_zones)
        if exit_zone is None:
            import logging
            logging.getLogger(__name__).warning(
                "[Re-ID] cam%s %s 출구존 미검출 (foot=%.3f,%.3f, zones=%d개)",
                camera_id, global_person_id, point[0], point[1], len(exit_zones),
            )
            return False
        import logging
        logging.getLogger(__name__).info(
            "[Re-ID] cam%s %s 출구 등록 (foot=%.3f,%.3f)",
            camera_id, global_person_id, point[0], point[1],
        )
        candidate = TransitionCandidate(
            global_person_id=global_person_id,
            source_camera_id=camera_id,
            exited_at=timestamp,
            embedding=embedding.copy(),
            quality=quality,
            direction_score=_direction_score(direction, point, exit_zone, entering=False),
        )
        with self._lock:
            self._pending = [
                item for item in self._pending
                if item.global_person_id != global_person_id
                and timestamp - item.exited_at <= REID_MAX_TRANSITION_SECONDS
            ]
            self._pending.append(candidate)
        return True

    def pending_count(self) -> int:
        now = time.monotonic()
        with self._lock:
            self._pending = [
                item for item in self._pending
                if now - item.exited_at <= REID_MAX_TRANSITION_SECONDS
            ]
            return len(self._pending)

    # ── 중복 시야(Overlap Zone) 실시간 매칭 ──────────────────────────────────

    def register_active_track(
        self,
        camera_id: int | None,
        local_track_id: int,
        global_person_id: str,
        embedding: np.ndarray,
        quality: float,
        timestamp: float,
        in_overlap_zone: bool,
    ) -> None:
        """카메라별 활성 트랙 정보를 갱신한다 (중복 시야 매칭 기반 데이터)."""
        cam_key = camera_id if camera_id is not None else -1
        with self._lock:
            cam = self._active_tracks.setdefault(cam_key, {})
            cam[local_track_id] = ActiveTrackInfo(
                global_person_id=self._resolve_id_nolock(global_person_id),
                embedding=embedding.copy(),
                quality=quality,
                timestamp=timestamp,
                in_overlap_zone=in_overlap_zone,
            )

    def purge_inactive_tracks(
        self,
        camera_id: int | None,
        active_local_ids: set[int],
        timestamp: float,
    ) -> None:
        """이번 프레임에 없어진 트랙을 레지스트리에서 제거한다."""
        cam_key = camera_id if camera_id is not None else -1
        stale_cutoff = OVERLAP_TIME_TOLERANCE * 3
        with self._lock:
            if cam_key in self._active_tracks:
                self._active_tracks[cam_key] = {
                    lid: info
                    for lid, info in self._active_tracks[cam_key].items()
                    if lid in active_local_ids
                    and timestamp - info.timestamp <= stale_cutoff
                }

    def try_overlap_match(
        self,
        camera_id: int | None,
        local_track_id: int,
        global_person_id: str,
        embedding: np.ndarray,
        quality: float,
        timestamp: float,
    ) -> str | None:
        """중복 시야 영역에서 다른 카메라 트랙과 비교하여 동일인이면 Global ID를 병합한다.

        Returns: 병합 후 canonical global_person_id (병합이 일어난 경우만), 아니면 None.
        """
        cam_key = camera_id if camera_id is not None else -1
        best_score = REID_OVERLAP_THRESHOLD - 0.001
        best_other_canonical: str | None = None

        with self._lock:
            canonical = self._resolve_id_nolock(global_person_id)

            for other_cam_key, other_tracks in self._active_tracks.items():
                if other_cam_key == cam_key:
                    continue
                for other_local_id, other_info in other_tracks.items():
                    if not other_info.in_overlap_zone:
                        continue
                    time_diff = abs(timestamp - other_info.timestamp)
                    if time_diff > OVERLAP_TIME_TOLERANCE:
                        continue
                    other_canonical = self._resolve_id_nolock(other_info.global_person_id)
                    if other_canonical == canonical:
                        continue  # 이미 같은 ID
                    sim = _cosine_similarity(embedding, other_info.embedding)
                    if sim < REID_STRONG_MATCH_THRESHOLD:
                        continue
                    # 70% similarity + 20% quality + 10% time proximity
                    time_score = max(0.0, 1.0 - time_diff / max(OVERLAP_TIME_TOLERANCE, 1e-6))
                    score = (
                        0.70 * sim
                        + 0.20 * min(quality, other_info.quality)
                        + 0.10 * time_score
                    )
                    if score > best_score:
                        best_score = score
                        best_other_canonical = other_canonical

            if best_other_canonical is None:
                return None

            # 더 낮은 번호(먼저 생성된) ID를 canonical로 유지
            try:
                a_num = int(canonical.split("-")[1])
                b_num = int(best_other_canonical.split("-")[1])
            except (IndexError, ValueError):
                a_num, b_num = 0, 1
            keep = canonical if a_num <= b_num else best_other_canonical
            drop = best_other_canonical if a_num <= b_num else canonical
            self._merge_ids_nolock(keep, drop)
            return keep

    # ── 헬멧 크로스카메라 집계 ───────────────────────────────────────────────

    def update_helmet(
        self,
        global_person_id: str,
        camera_id: int | None,
        helmet_on: bool,
        quality: float,
        timestamp: float,
    ) -> None:
        """카메라 프레임의 헬멧 감지 결과를 Global ID에 누적한다."""
        with self._lock:
            canonical = self._resolve_id_nolock(global_person_id)
            obs = self._helmet_observations.setdefault(canonical, deque(maxlen=50))
            obs.append((timestamp, camera_id, helmet_on, quality))

    def get_helmet_status(self, global_person_id: str) -> bool | None:
        """최근 HELMET_VOTE_WINDOW 초의 품질 가중 다수결로 헬멧 착용 여부를 반환한다.

        단일 카메라만 관측 중일 때는 None을 반환해 단순 frame-level 결과를 사용하게 한다.
        복수 카메라 데이터가 있을 때만 크로스카메라 집계를 사용한다.
        """
        with self._lock:
            canonical = self._resolve_id_nolock(global_person_id)
            obs = self._helmet_observations.get(canonical)
            if not obs:
                return None
            now = time.monotonic()
            recent = [(ts, cam, h, q) for ts, cam, h, q in obs if now - ts <= HELMET_VOTE_WINDOW_SECONDS]
            if not recent:
                return None
            # 복수 카메라 관측이 있을 때만 집계 결과 사용
            cameras_seen = {cam for _, cam, _, _ in recent}
            if len(cameras_seen) < 2:
                return None
            on_weight = sum(q for _, _, h, q in recent if h)
            off_weight = sum(q for _, _, h, q in recent if not h)
            total = on_weight + off_weight
            if total < 0.01:
                return None
            return on_weight >= off_weight


@dataclass
class LocalTrack:
    local_track_id: int
    global_person_id: str
    box: list[float]
    confidence: float
    embedding: np.ndarray
    quality: float
    reid_backend: str = "appearance"
    points: list[tuple[float, float]] = field(default_factory=list)
    missed: int = 0
    match_details: dict | None = None
    age_frames: int = 1
    entry_point: tuple[float, float] | None = None
    entry_grace_remaining: int = 0
    exit_point: tuple[float, float] | None = None
    exit_direction: tuple[float, float] = (0.0, 0.0)
    exit_candidate_registered: bool = False
    in_overlap_zone: bool = False
    # (timestamp, 'sun'|'shade'|'unknown') 최근 10초 이력
    _shade_history: list = field(default_factory=list)
    # 멀티프레임 embedding 안정화 이력
    _emb_history: EmbeddingHistory = field(default_factory=EmbeddingHistory)

    @property
    def point(self) -> tuple[float, float]:
        return self.points[-1]

    @property
    def direction(self) -> tuple[float, float]:
        if len(self.points) < 2:
            return (0.0, 0.0)
        start = self.points[max(0, len(self.points) - 6)]
        end = self.points[-1]
        return _unit((end[0] - start[0], end[1] - start[1]))

    @property
    def shade_status(self) -> str:
        """최근 5초 다수결로 양지/그늘 판정."""
        now = time.monotonic()
        recent = [s for t, s in self._shade_history if now - t <= 5.0]
        if not recent:
            return 'unknown'
        sun = recent.count('sun')
        shade = recent.count('shade')
        total = len(recent)
        if sun > total // 2:
            return 'sun'
        if shade > total // 2:
            return 'shade'
        return 'unknown'

    def update_shade(self, status: str) -> None:
        now = time.monotonic()
        self._shade_history.append((now, status))
        cutoff = now - 10.0
        self._shade_history = [(t, s) for t, s in self._shade_history if t >= cutoff]

    def update_embedding_stable(self, new_embedding: np.ndarray, new_quality: float) -> None:
        """EmbeddingHistory를 활용한 멀티프레임 안정화 embedding 업데이트."""
        self._emb_history.add(new_embedding, new_quality)
        rep = self._emb_history.representative()
        if rep is not None and new_quality >= self.quality * 0.8:
            # 대표 embedding(히스토리 평균)과 현재 EMA를 절충
            mixed = self.embedding * 0.60 + rep * 0.25 + new_embedding * 0.15
            norm = float(np.linalg.norm(mixed))
            self.embedding = mixed / norm if norm > 1e-8 else mixed
        elif new_quality >= self.quality * 0.8:
            mixed = self.embedding * 0.75 + new_embedding * 0.25
            norm = float(np.linalg.norm(mixed))
            self.embedding = mixed / norm if norm > 1e-8 else mixed
        self.quality = max(self.quality * 0.98, new_quality)


class HeatExposureTracker:
    """global_person_id별 폭염구역 연속 체류 시간을 추적한다 (크로스카메라)."""

    ABSENCE_RESET_SECONDS = 20.0
    # 중복 시야에서 OR 판정용 투표 유효 시간 — 이 창 안에 어느 카메라라도 양지면 양지로 처리
    SUN_VOTE_WINDOW = 2.0

    def __init__(self):
        self._lock = threading.Lock()
        # global_person_id -> {"last_seen", "heat_start", "camera_votes": {cam_id: (ts, bool)}}
        self._state: dict[str, dict] = {}

    def update(
        self,
        global_person_id: str,
        in_heat: bool,
        now: float | None = None,
        camera_id=None,
    ) -> float:
        """폭염구역 체류 상태를 갱신하고 연속 체류 초를 반환한다.

        camera_id를 전달하면 중복 시야 OR 로직이 활성화된다:
        여러 카메라 중 하나라도 양지로 판단하면 양지로 처리한다.
        """
        if now is None:
            now = time.monotonic()
        with self._lock:
            state = self._state.get(global_person_id)
            if state is None:
                state = {
                    "last_seen": now,
                    "heat_start": now if in_heat else None,
                    "camera_votes": {},
                }
                self._state[global_person_id] = state
            else:
                gap = now - state["last_seen"]
                state["last_seen"] = now
                if gap >= self.ABSENCE_RESET_SECONDS:
                    # 오래 자리를 비웠으면 투표 이력도 초기화
                    state["heat_start"] = None
                    state.setdefault("camera_votes", {}).clear()

            # 이번 카메라 판정 기록
            if camera_id is not None:
                state.setdefault("camera_votes", {})[camera_id] = (now, in_heat)

            # OR 로직: SUN_VOTE_WINDOW 내에 하나라도 양지 판정이 있으면 양지로 확정
            votes = state.get("camera_votes", {})
            effective_in_heat = in_heat or any(
                vote
                for ts, vote in votes.values()
                if vote and now - ts <= self.SUN_VOTE_WINDOW
            )

            if effective_in_heat:
                if state.get("heat_start") is None:
                    state["heat_start"] = now
            else:
                state["heat_start"] = None

            if effective_in_heat and state.get("heat_start") is not None:
                return now - state["heat_start"]
            return 0.0

    def merge_ids(self, old_id: str, new_id: str) -> None:
        """ID 병합 시 old_id의 열 노출 누적 시간을 new_id로 이전한다."""
        with self._lock:
            old_state = self._state.pop(old_id, None)
            if old_state is None:
                return
            new_state = self._state.get(new_id)
            if new_state is None:
                self._state[new_id] = old_state
            else:
                # 더 이른 heat_start를 채택해 누적 시간을 최대한 보존
                if (
                    old_state.get("heat_start") is not None
                    and (
                        new_state.get("heat_start") is None
                        or old_state["heat_start"] < new_state["heat_start"]
                    )
                ):
                    new_state["heat_start"] = old_state["heat_start"]
                new_state["last_seen"] = max(
                    old_state.get("last_seen", 0.0),
                    new_state.get("last_seen", 0.0),
                )
                # camera_votes 병합 (최신 판정 유지)
                old_votes = old_state.get("camera_votes", {})
                new_votes = new_state.setdefault("camera_votes", {})
                for cam, (ts, vote) in old_votes.items():
                    if cam not in new_votes or new_votes[cam][0] < ts:
                        new_votes[cam] = (ts, vote)

    def purge_stale(self, now: float | None = None):
        if now is None:
            now = time.monotonic()
        with self._lock:
            cutoff = now - 60.0
            self._state = {gid: s for gid, s in self._state.items() if s["last_seen"] >= cutoff}


class CameraPersonTracker:
    def __init__(self, camera_id: int | None, identity_manager: GlobalIdentityManager):
        self.camera_id = camera_id
        self.identity_manager = identity_manager
        self._next_local_id = 1
        self._tracks: dict[int, LocalTrack] = {}

    @staticmethod
    def _point(box: list[float], width: int, height: int) -> tuple[float, float]:
        return (((box[0] + box[2]) / 2.0) / width, box[3] / height)

    def update(
        self,
        frame: np.ndarray,
        detections: list[dict],
        width: int,
        height: int,
        entry_zones: list[dict],
        exit_zones: list[dict],
        overlap_zones: list[dict] | None = None,
        timestamp: float | None = None,
    ) -> list[LocalTrack]:
        timestamp = timestamp if timestamp is not None else time.monotonic()
        overlap_zones = overlap_zones or []

        # Phase 1 — 관측값 구성
        observations = []
        for detection in detections:
            color_embedding, visual_quality = appearance_embedding(frame, detection["box"])
            embedding = combined_appearance_embedding(
                color_embedding,
                detection.get("reid_embedding"),
            )
            quality = 0.8 * visual_quality + 0.2 * float(detection.get("conf", 0.0))
            observations.append({
                "detection": detection,
                "embedding": embedding,
                "quality": quality,
                "point": self._point(detection["box"], width, height),
                "reid_backend": detection.get("reid_backend", "appearance"),
            })

        # Phase 2 — 기존 트랙과 관측값 매칭
        pairs: list[tuple[float, int, int]] = []
        diagonal = math.hypot(width, height)
        for local_id, track in self._tracks.items():
            old_center = ((track.box[0] + track.box[2]) / 2.0, (track.box[1] + track.box[3]) / 2.0)
            for observation_index, observation in enumerate(observations):
                box = observation["detection"]["box"]
                center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
                distance = math.hypot(center[0] - old_center[0], center[1] - old_center[1]) / diagonal
                overlap = _iou(track.box, box)
                appearance = _cosine_similarity(track.embedding, observation["embedding"])
                score = 0.55 * overlap + 0.30 * appearance + 0.15 * max(0.0, 1.0 - distance / 0.25)
                if overlap >= 0.08 or distance <= 0.12:
                    pairs.append((score, local_id, observation_index))

        # Phase 3 — 매칭된 트랙 업데이트
        matched_tracks: set[int] = set()
        matched_observations: set[int] = set()
        for _, local_id, observation_index in sorted(pairs, reverse=True):
            if local_id in matched_tracks or observation_index in matched_observations:
                continue
            track = self._tracks[local_id]
            observation = observations[observation_index]
            detection = observation["detection"]

            # 다른 카메라에서 발생한 ID 병합을 반영한다
            canonical = self.identity_manager.resolve_id(track.global_person_id)
            if canonical != track.global_person_id:
                track.global_person_id = canonical

            track.box = detection["box"]
            track.confidence = float(detection.get("conf", 0.0))
            track.reid_backend = observation["reid_backend"]
            track.points.append(observation["point"])
            track.points = track.points[-20:]
            track.missed = 0
            track.age_frames += 1

            # 멀티프레임 안정화 embedding 업데이트
            track.update_embedding_stable(observation["embedding"], observation["quality"])

            if track.entry_point is None and _point_in_any(track.point, entry_zones):
                track.entry_point = track.point
                track.entry_grace_remaining = REID_ENTRY_GRACE_FRAMES
            if _point_in_any(track.point, exit_zones):
                track.exit_point = track.point
                track.exit_direction = track.direction
                if not track.exit_candidate_registered:
                    track.exit_candidate_registered = self.identity_manager.register_departure(
                        track.global_person_id,
                        self.camera_id,
                        track.embedding,
                        track.quality,
                        track.exit_point,
                        track.exit_direction,
                        exit_zones,
                        timestamp,
                    )

            if (
                track.match_details is None
                and track.entry_point is not None
                and track.entry_grace_remaining > 0
            ):
                matched = self.identity_manager.try_transition_match(
                    self.camera_id,
                    track.embedding,
                    track.quality,
                    track.entry_point,
                    track.direction,
                    entry_zones,
                    timestamp,
                )
                if matched is not None:
                    track.global_person_id, track.match_details = matched
                track.entry_grace_remaining -= 1

            self.identity_manager.update_embedding(
                track.global_person_id, track.embedding, track.quality
            )
            matched_tracks.add(local_id)
            matched_observations.add(observation_index)

        # Phase 4 — 놓친 트랙 처리
        for local_id, track in list(self._tracks.items()):
            if local_id in matched_tracks:
                continue
            track.missed += 1
            if track.missed > TRACK_MAX_MISSED_FRAMES:
                if not track.exit_candidate_registered:
                    self.identity_manager.register_departure(
                        track.global_person_id,
                        self.camera_id,
                        track.embedding,
                        track.quality,
                        track.exit_point or track.point,
                        track.exit_direction if track.exit_point else track.direction,
                        exit_zones,
                        timestamp,
                    )
                del self._tracks[local_id]

        # Phase 5 — 새 트랙 생성
        for index, observation in enumerate(observations):
            if index in matched_observations:
                continue
            global_id, match_details = self.identity_manager.assign(
                self.camera_id,
                observation["embedding"],
                observation["quality"],
                observation["point"],
                (0.0, 0.0),
                entry_zones,
                timestamp,
            )
            local_id = self._next_local_id
            self._next_local_id += 1
            detection = observation["detection"]
            entry_point = (
                observation["point"]
                if _point_in_any(observation["point"], entry_zones)
                else None
            )
            exit_point = (
                observation["point"]
                if _point_in_any(observation["point"], exit_zones)
                else None
            )
            new_track = LocalTrack(
                local_track_id=local_id,
                global_person_id=global_id,
                box=detection["box"],
                confidence=float(detection.get("conf", 0.0)),
                embedding=observation["embedding"],
                quality=observation["quality"],
                reid_backend=observation["reid_backend"],
                points=[observation["point"]],
                match_details=match_details,
                entry_point=entry_point,
                entry_grace_remaining=REID_ENTRY_GRACE_FRAMES if entry_point else 0,
                exit_point=exit_point,
            )
            if exit_point is not None:
                new_track.exit_candidate_registered = self.identity_manager.register_departure(
                    new_track.global_person_id,
                    self.camera_id,
                    new_track.embedding,
                    new_track.quality,
                    exit_point,
                    new_track.exit_direction,
                    exit_zones,
                    timestamp,
                )
            self._tracks[local_id] = new_track
            matched_tracks.add(local_id)

        # Phase 6 — 중복 시야(Overlap Zone) 실시간 매칭 및 활성 트랙 등록
        active_local_ids: set[int] = set()
        for local_id, track in self._tracks.items():
            in_overlap = _point_in_any(track.point, overlap_zones) is not None
            track.in_overlap_zone = in_overlap
            active_local_ids.add(local_id)

            self.identity_manager.register_active_track(
                self.camera_id,
                local_id,
                track.global_person_id,
                track.embedding,
                track.quality,
                timestamp,
                in_overlap,
            )

        # 중복 시야 영역의 트랙에 대해 다른 카메라 트랙과 실시간 매칭
        for local_id, track in list(self._tracks.items()):
            if not track.in_overlap_zone or track.quality < EMBEDDING_MIN_QUALITY:
                continue
            canonical = self.identity_manager.try_overlap_match(
                self.camera_id,
                local_id,
                track.global_person_id,
                track.embedding,
                track.quality,
                timestamp,
            )
            if canonical is not None and canonical != track.global_person_id:
                track.global_person_id = canonical
                if track.match_details is None:
                    track.match_details = {"merged_via": "overlap_zone"}

        self.identity_manager.purge_inactive_tracks(self.camera_id, active_local_ids, timestamp)

        return [self._tracks[local_id] for local_id in sorted(matched_tracks) if local_id in self._tracks]

    def flush(self, exit_zones: list[dict], timestamp: float | None = None):
        timestamp = timestamp if timestamp is not None else time.monotonic()
        for track in list(self._tracks.values()):
            if not track.exit_candidate_registered:
                self.identity_manager.register_departure(
                    track.global_person_id,
                    self.camera_id,
                    track.embedding,
                    track.quality,
                    track.exit_point or track.point,
                    track.exit_direction if track.exit_point else track.direction,
                    exit_zones,
                    timestamp,
                )
        self._tracks.clear()
