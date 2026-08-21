"""BoT-SORT single-camera person tracking, ReID identity memory, and heat state."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment
from ultralytics.trackers.bot_sort import BOTSORT

from ..config import (
    REID_GALLERY_SECONDS,
    REID_MATCH_THRESHOLD,
    TRACK_BUFFER_FRAMES,
)
from .reid_encoder import reid_encoder


logger = logging.getLogger(__name__)


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        return 0.0
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-8:
        return 0.0
    return float(np.clip(np.dot(left, right) / denominator, 0.0, 1.0))


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


def _center(box: list[float]) -> np.ndarray:
    return np.asarray(
        [(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0],
        dtype=np.float32,
    )


def appearance_embedding(frame: np.ndarray, box: list[float]) -> tuple[np.ndarray, float]:
    """Compact color fallback and crop-quality score."""
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
            features.append(cv2.calcHist([stripe], [channel], None, [8], [0, limit]).ravel())
    for channel in range(3):
        features.append(cv2.calcHist([lab], [channel], None, [16], [0, 256]).ravel())

    embedding = np.concatenate(features).astype(np.float32)
    norm = float(np.linalg.norm(embedding))
    if norm > 1e-8:
        embedding /= norm

    crop_area = (x2 - x1) * (y2 - y1)
    area_score = min(1.0, crop_area / max(1.0, width * height * 0.08))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    sharpness_score = min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 160.0)
    brightness_score = max(0.0, 1.0 - abs(float(gray.mean()) - 128.0) / 128.0)
    quality = 0.45 * area_score + 0.35 * sharpness_score + 0.20 * brightness_score
    return embedding, round(float(quality), 4)


class _Detections:
    def __init__(self, xyxy: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xyxy = xyxy.astype(np.float32, copy=False)
        self.conf = conf.astype(np.float32, copy=False)
        self.cls = cls.astype(np.float32, copy=False)

    @property
    def xywh(self) -> np.ndarray:
        if not len(self.xyxy):
            return np.empty((0, 4), dtype=np.float32)
        out = self.xyxy.copy()
        out[:, 0] = (self.xyxy[:, 0] + self.xyxy[:, 2]) / 2
        out[:, 1] = (self.xyxy[:, 1] + self.xyxy[:, 3]) / 2
        out[:, 2] = self.xyxy[:, 2] - self.xyxy[:, 0]
        out[:, 3] = self.xyxy[:, 3] - self.xyxy[:, 1]
        return out

    def __len__(self):
        return len(self.conf)

    def __getitem__(self, index):
        return _Detections(self.xyxy[index], self.conf[index], self.cls[index])


class _SessionBOTSORT(BOTSORT):
    """Do not reset Ultralytics' process-global ID counter per demo session."""

    @staticmethod
    def reset_id() -> None:
        return None


@dataclass
class LocalTrack:
    local_track_id: int
    track_id: str
    box: list[float]
    confidence: float
    embedding: np.ndarray
    quality: float
    point: tuple[float, float]
    missed: int = 0
    age_frames: int = 1
    ble_tag_key: str | None = None
    last_seen_at: float = field(default_factory=time.monotonic)
    velocity: np.ndarray = field(
        default_factory=lambda: np.zeros(4, dtype=np.float32),
        repr=False,
    )
    embedding_gallery: list[np.ndarray] = field(default_factory=list, repr=False)
    _shade_history: list[tuple[float, str]] = field(default_factory=list)

    MAX_GALLERY_SIZE = 12

    def __post_init__(self) -> None:
        if not self.embedding_gallery and self.embedding.size:
            self.embedding_gallery.append(self.embedding.copy())

    @property
    def shade_status(self) -> str:
        now = time.monotonic()
        recent = [status for timestamp, status in self._shade_history if now - timestamp <= 5.0]
        if not recent:
            return "unknown"
        sun = recent.count("sun")
        shade = recent.count("shade")
        if sun > len(recent) // 2:
            return "sun"
        if shade > len(recent) // 2:
            return "shade"
        return "unknown"

    def update_shade(self, status: str) -> None:
        now = time.monotonic()
        self._shade_history.append((now, status))
        self._shade_history = [item for item in self._shade_history if now - item[0] <= 10.0]

    def predicted_box(self, now: float) -> list[float]:
        elapsed = min(2.0, max(0.0, now - self.last_seen_at))
        predicted = np.asarray(self.box, dtype=np.float32) + self.velocity * elapsed
        return predicted.tolist()

    def update_motion(self, box: list[float], now: float) -> None:
        elapsed = now - self.last_seen_at
        if elapsed >= 0.02:
            measured = (
                np.asarray(box, dtype=np.float32) - np.asarray(self.box, dtype=np.float32)
            ) / elapsed
            measured = np.clip(measured, -2000.0, 2000.0)
            if np.any(self.velocity):
                self.velocity = self.velocity * 0.65 + measured * 0.35
            else:
                self.velocity = measured
        self.box = box
        self.last_seen_at = now

    def appearance_similarity(self, embedding: np.ndarray) -> float:
        gallery = self.embedding_gallery or [self.embedding]
        scores = sorted(
            (_cosine_similarity(saved, embedding) for saved in gallery),
            reverse=True,
        )
        strongest = scores[: min(3, len(scores))]
        return float(sum(strongest) / len(strongest)) if strongest else 0.0

    def update_embedding(
        self,
        embedding: np.ndarray,
        quality: float,
        *,
        allow_gallery_update: bool = True,
    ) -> None:
        if not allow_gallery_update:
            self.quality *= 0.995
            return
        if self.embedding.shape != embedding.shape:
            self.embedding = embedding.copy()
            self.embedding_gallery = [embedding.copy()] if quality >= 0.45 else []
        elif quality >= self.quality * 0.8:
            mixed = self.embedding * 0.8 + embedding * 0.2
            norm = float(np.linalg.norm(mixed))
            self.embedding = mixed / norm if norm > 1e-8 else mixed
            if quality >= 0.45:
                self.embedding_gallery.append(embedding.copy())
                self.embedding_gallery = self.embedding_gallery[-self.MAX_GALLERY_SIZE:]
        self.quality = max(self.quality * 0.98, quality)


class HeatExposureTracker:
    OUTSIDE_RESET_SECONDS = 10.0
    MAX_OBSERVED_GAP_SECONDS = 2.0
    STALE_SECONDS = 60.0
    CLEANUP_INTERVAL_SECONDS = 30.0

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, float | None]] = {}
        self._last_cleanup = 0.0

    def update(self, track_id: str, in_heat: bool, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            if now - self._last_cleanup >= self.CLEANUP_INTERVAL_SECONDS:
                self._purge_stale_locked(now)
                self._last_cleanup = now
            state = self._state.get(track_id)
            if state is None:
                state = {
                    "last_seen": now,
                    "last_in_heat": in_heat,
                    "outside_since": None if in_heat else now,
                    "accumulated_seconds": 0.0,
                }
                self._state[track_id] = state
            else:
                last_seen = float(state["last_seen"] or now)
                elapsed = max(0.0, now - last_seen)
                if state["last_in_heat"] and elapsed <= self.MAX_OBSERVED_GAP_SECONDS:
                    state["accumulated_seconds"] = float(state["accumulated_seconds"] or 0.0) + elapsed
                if in_heat:
                    state["outside_since"] = None
                else:
                    if state["last_in_heat"]:
                        state["outside_since"] = now
                    outside_since = state["outside_since"]
                    if outside_since is not None and now - float(outside_since) >= self.OUTSIDE_RESET_SECONDS:
                        state["accumulated_seconds"] = 0.0
                state["last_seen"] = now
                state["last_in_heat"] = in_heat
            return float(state["accumulated_seconds"] or 0.0)

    def purge_stale(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge_stale_locked(now)
            self._last_cleanup = now

    def _purge_stale_locked(self, now: float) -> None:
        self._state = {
            track_id: state
            for track_id, state in self._state.items()
            if now - float(state["last_seen"] or 0.0) < self.STALE_SECONDS
        }

    def flush(self) -> None:
        with self._lock:
            self._state.clear()
            self._last_cleanup = 0.0


class CameraPersonTracker:
    """BoT-SORT tracking with FastReID gallery and optional BLE identity hints."""

    def __init__(self):
        args = SimpleNamespace(
            track_high_thresh=0.25,
            track_low_thresh=0.1,
            new_track_thresh=0.25,
            track_buffer=max(1, TRACK_BUFFER_FRAMES),
            match_thresh=0.8,
            fuse_score=True,
            gmc_method=None,
            proximity_thresh=0.35,
            appearance_thresh=0.65,
            with_reid=True,
            model="auto",
            device=None,
        )
        self._tracker = _SessionBOTSORT(args)
        self._lock = threading.RLock()
        self._next_local_id = 1
        self._identities: dict[int, LocalTrack] = {}
        self._bot_to_local: dict[int, int] = {}
        self._active_ble_tags: set[str] = set()

    OVERLAP_FREEZE_IOU = 0.18
    GLOBAL_MATCH_THRESHOLD = 0.42
    MIN_APPEARANCE_GATE = 0.45
    STALE_OUTPUT_SECONDS = 1.0

    @staticmethod
    def _point(box: list[float], width: int, height: int) -> tuple[float, float]:
        return (((box[0] + box[2]) / 2.0) / width, box[3] / height)

    def set_active_ble_tags(self, tag_keys: set[str]) -> None:
        with self._lock:
            self._active_ble_tags = set(tag_keys)

    def bind_tag(self, tag_key: str, track_id: str) -> bool:
        with self._lock:
            target = next((track for track in self._identities.values() if track.track_id == track_id), None)
            if target is None:
                return False
            for track in self._identities.values():
                if track.ble_tag_key == tag_key or track.local_track_id == target.local_track_id:
                    track.ble_tag_key = None
            target.ble_tag_key = tag_key
            return True

    def unbind_tag(self, tag_key: str) -> None:
        with self._lock:
            for track in self._identities.values():
                if track.ble_tag_key == tag_key:
                    track.ble_tag_key = None

    @staticmethod
    def _motion_similarity(
        track: LocalTrack,
        box: list[float],
        now: float,
        frame_diagonal: float,
    ) -> tuple[float, float]:
        predicted = track.predicted_box(now)
        distance = float(np.linalg.norm(_center(predicted) - _center(box)))
        box_width = max(1.0, predicted[2] - predicted[0])
        box_height = max(1.0, predicted[3] - predicted[1])
        track_scale = max(
            float(np.sqrt(box_width * box_height)) * 0.75,
            frame_diagonal * 0.04,
            1.0,
        )
        ratio = distance / track_scale
        return float(np.exp(-0.5 * ratio * ratio)), _iou(predicted, box)

    def _assign_identities(
        self,
        observations: list[dict],
        now: float,
        width: int,
        height: int,
    ) -> dict[int, int]:
        candidates = [
            track for track in self._identities.values()
            if now - track.last_seen_at <= REID_GALLERY_SECONDS
        ]
        if not candidates or not observations:
            return {}

        frame_diagonal = max(1.0, float(np.hypot(width, height)))
        scores = np.full((len(candidates), len(observations)), -1.0, dtype=np.float32)
        active_bound = [
            track for track in candidates
            if track.ble_tag_key and track.ble_tag_key in self._active_ble_tags
        ]

        for candidate_index, track in enumerate(candidates):
            for observation_index, observation in enumerate(observations):
                appearance = track.appearance_similarity(observation["embedding"])
                motion, overlap = self._motion_similarity(
                    track,
                    observation["box"],
                    now,
                    frame_diagonal,
                )
                same_bot = self._bot_to_local.get(observation["bot_id"]) == track.local_track_id
                recently_seen = now - track.last_seen_at <= 1.5

                score = (
                    0.55 * appearance
                    + 0.28 * motion
                    + 0.12 * overlap
                    + (0.05 if same_bot else 0.0)
                )
                if track.ble_tag_key in self._active_ble_tags:
                    score += 0.02
                    if len(active_bound) == 1 and appearance >= 0.35:
                        score += 0.06

                allowed = (
                    same_bot
                    or appearance >= self.MIN_APPEARANCE_GATE
                    or (recently_seen and (motion >= 0.18 or overlap >= 0.03))
                )
                if allowed:
                    scores[candidate_index, observation_index] = score

        row_indexes, column_indexes = linear_sum_assignment(-scores)
        assigned: dict[int, int] = {}
        for row_index, column_index in zip(row_indexes, column_indexes):
            score = float(scores[row_index, column_index])
            if score < self.GLOBAL_MATCH_THRESHOLD:
                continue
            candidate = candidates[row_index]
            observation = observations[column_index]
            appearance = candidate.appearance_similarity(observation["embedding"])
            same_bot = self._bot_to_local.get(observation["bot_id"]) == candidate.local_track_id
            if now - candidate.last_seen_at > 1.5 and not same_bot:
                if appearance < REID_MATCH_THRESHOLD:
                    continue
            assigned[column_index] = candidate.local_track_id
        return assigned

    def predict(
        self,
        *,
        timestamp: float | None = None,
    ) -> list[LocalTrack]:
        """Return recent identities without feeding stale detections to BoT-SORT."""
        now = time.monotonic() if timestamp is None else timestamp
        with self._lock:
            return sorted(
                (
                    track for track in self._identities.values()
                    if now - track.last_seen_at <= self.STALE_OUTPUT_SECONDS
                ),
                key=lambda track: track.local_track_id,
            )

    def update(
        self,
        frame: np.ndarray,
        detections: list[dict],
        width: int,
        height: int,
        timestamp: float | None = None,
    ) -> list[LocalTrack]:
        now = time.monotonic() if timestamp is None else timestamp
        boxes = [list(map(float, detection["box"])) for detection in detections]
        color = [appearance_embedding(frame, box) for box in boxes]
        deep = reid_encoder.extract(frame, boxes)
        embeddings = deep if deep is not None else np.asarray([item[0] for item in color], dtype=np.float32)
        qualities = [0.8 * item[1] + 0.2 * float(d.get("conf", 0.0)) for item, d in zip(color, detections)]

        xyxy = np.asarray(boxes, dtype=np.float32).reshape((-1, 4))
        conf = np.asarray([d.get("conf", 0.0) for d in detections], dtype=np.float32)
        classes = np.zeros(len(detections), dtype=np.float32)
        results = _Detections(xyxy, conf, classes)

        with self._lock:
            tracked = self._tracker.update(results, img=frame, feats=embeddings)
            observations: list[dict] = []
            for row in tracked:
                detection_index = int(row[7])
                if detection_index < 0 or detection_index >= len(detections):
                    continue
                observations.append({
                    "box": [float(value) for value in row[:4]],
                    "bot_id": int(row[4]),
                    "confidence": float(row[5]),
                    "embedding": embeddings[detection_index],
                    "quality": qualities[detection_index],
                })

            overlapping: set[int] = set()
            for left_index, left in enumerate(observations):
                for right_index in range(left_index + 1, len(observations)):
                    if _iou(left["box"], observations[right_index]["box"]) >= self.OVERLAP_FREEZE_IOU:
                        overlapping.update((left_index, right_index))

            assigned = self._assign_identities(observations, now, width, height)
            output: list[LocalTrack] = []

            for observation_index, observation in enumerate(observations):
                box = observation["box"]
                embedding = observation["embedding"]
                quality = observation["quality"]
                local_id = assigned.get(observation_index)
                if local_id is None:
                    local_id = self._next_local_id
                    self._next_local_id += 1

                previous_local_id = self._bot_to_local.get(observation["bot_id"])
                if previous_local_id is not None and previous_local_id != local_id:
                    logger.info(
                        "tracker identity remap: bot_id=%s local_id=%s->%s",
                        observation["bot_id"],
                        previous_local_id,
                        local_id,
                    )

                track = self._identities.get(local_id)
                if track is None:
                    track = LocalTrack(
                        local_track_id=local_id,
                        track_id=f"person-{local_id:06d}",
                        box=box,
                        confidence=observation["confidence"],
                        embedding=embedding.copy(),
                        quality=quality,
                        point=self._point(box, width, height),
                        last_seen_at=now,
                    )
                    self._identities[local_id] = track
                else:
                    track.update_motion(box, now)
                    track.confidence = observation["confidence"]
                    track.point = self._point(box, width, height)
                    track.missed = 0
                    track.age_frames += 1
                    track.update_embedding(
                        embedding,
                        quality,
                        allow_gallery_update=observation_index not in overlapping,
                    )

                output.append(track)

            current_bots = {observation["bot_id"] for observation in observations}
            current_locals = {track.local_track_id for track in output}
            self._bot_to_local = {
                bot_id: local_id
                for bot_id, local_id in self._bot_to_local.items()
                if bot_id not in current_bots and local_id not in current_locals
            }
            self._bot_to_local.update({
                observation["bot_id"]: track.local_track_id
                for observation, track in zip(observations, output)
            })

            for local_id, track in list(self._identities.items()):
                if local_id not in current_locals:
                    track.missed += 1
                if (
                    not track.ble_tag_key
                    and now - track.last_seen_at > REID_GALLERY_SECONDS
                ):
                    self._identities.pop(local_id, None)
                    for bot_id, mapped_local in list(self._bot_to_local.items()):
                        if mapped_local == local_id:
                            del self._bot_to_local[bot_id]

            return sorted(output, key=lambda track: track.local_track_id)

    def flush(self) -> None:
        with self._lock:
            self._tracker.reset()
            self._identities.clear()
            self._bot_to_local.clear()
            self._active_ble_tags.clear()
            self._next_local_id = 1
