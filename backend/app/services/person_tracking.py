"""Camera-local person tracking and cross-camera identity hand-off."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
from shapely.geometry import Point

from ..config import (
    REID_DEEP_WEIGHT,
    REID_ENTRY_GRACE_FRAMES,
    REID_MAX_TRANSITION_SECONDS,
    REID_MIN_SIMILARITY,
    REID_ROI_MARGIN,
    REID_SCORE_THRESHOLD,
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
    """Build a normalized person appearance descriptor and crop quality score.

    The descriptor uses spatial HSV/Lab histograms so it works offline. The
    matching layer is intentionally isolated, allowing a learned OSNet/FastReID
    embedding to replace this function without changing the tracking flow.
    """
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
        # Preserve a fixed vector layout while keeping color-only fallback useful.
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


@dataclass
class TransitionCandidate:
    global_person_id: str
    source_camera_id: int | None
    exited_at: float
    embedding: np.ndarray
    quality: float
    direction_score: float


class GlobalIdentityManager:
    """Site-scoped global identity state shared by every camera service."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_global_id = 1
        self._pending: list[TransitionCandidate] = []
        self._embeddings: dict[str, np.ndarray] = {}

    def _new_id(self) -> str:
        identity = f"person-{self._next_global_id:06d}"
        self._next_global_id += 1
        return identity

    def _purge_expired(self, timestamp: float):
        self._pending = [
            item for item in self._pending
            if timestamp - item.exited_at <= REID_MAX_TRANSITION_SECONDS
        ]

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
            # 진입 방향을 모를 때(0.5 기본값) 패널티를 주지 않도록 후보 방향만 사용
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
                camera_id,
                embedding,
                quality,
                point,
                direction,
                entry_zones,
                timestamp,
            )
            if matched is not None:
                return matched

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
            return self._match_pending(
                camera_id,
                embedding,
                quality,
                entry_point,
                direction,
                entry_zones,
                timestamp,
            )

    def update_embedding(self, global_person_id: str, embedding: np.ndarray, quality: float):
        if quality < 0.35:
            return
        with self._lock:
            current = self._embeddings.get(global_person_id)
            mixed = embedding if current is None else current * 0.7 + embedding * 0.3
            norm = float(np.linalg.norm(mixed))
            self._embeddings[global_person_id] = mixed / norm if norm > 1e-8 else mixed

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
            "[Re-ID] cam%s %s 출구 등록 → 전환대기 추가 (foot=%.3f,%.3f)",
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
        timestamp: float | None = None,
    ) -> list[LocalTrack]:
        timestamp = timestamp if timestamp is not None else time.monotonic()
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

        matched_tracks: set[int] = set()
        matched_observations: set[int] = set()
        for _, local_id, observation_index in sorted(pairs, reverse=True):
            if local_id in matched_tracks or observation_index in matched_observations:
                continue
            track = self._tracks[local_id]
            observation = observations[observation_index]
            detection = observation["detection"]
            track.box = detection["box"]
            track.confidence = float(detection.get("conf", 0.0))
            track.reid_backend = observation["reid_backend"]
            track.points.append(observation["point"])
            track.points = track.points[-20:]
            track.missed = 0
            track.age_frames += 1
            if observation["quality"] >= track.quality * 0.8:
                mixed = track.embedding * 0.75 + observation["embedding"] * 0.25
                norm = float(np.linalg.norm(mixed))
                track.embedding = mixed / norm if norm > 1e-8 else mixed
            track.quality = max(track.quality * 0.98, observation["quality"])
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

        return [self._tracks[local_id] for local_id in sorted(matched_tracks)]

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
