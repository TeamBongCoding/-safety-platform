"""Single-camera person tracking and heat-exposure state."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import TRACK_MAX_MISSED_FRAMES


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-8:
        return 0.0
    return float(np.clip(np.dot(left, right) / denominator, 0.0, 1.0))


def appearance_embedding(frame: np.ndarray, box: list[float]) -> tuple[np.ndarray, float]:
    """Build a compact color descriptor used only for local track association."""
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
    _shade_history: list[tuple[float, str]] = field(default_factory=list)

    @property
    def shade_status(self) -> str:
        """Return the majority sun/shade observation from the last five seconds."""
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
        self._shade_history = [
            (timestamp, value)
            for timestamp, value in self._shade_history
            if now - timestamp <= 10.0
        ]

    def update_embedding(self, embedding: np.ndarray, quality: float) -> None:
        if quality >= self.quality * 0.8:
            mixed = self.embedding * 0.75 + embedding * 0.25
            norm = float(np.linalg.norm(mixed))
            self.embedding = mixed / norm if norm > 1e-8 else mixed
        self.quality = max(self.quality * 0.98, quality)


class HeatExposureTracker:
    """Track cumulative heat-zone exposure for a person in one camera stream."""

    OUTSIDE_RESET_SECONDS = 10.0
    MAX_OBSERVED_GAP_SECONDS = 2.0

    def __init__(self):
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, float | None]] = {}

    def update(
        self,
        track_id: str,
        in_heat: bool,
        now: float | None = None,
    ) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
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
                    state["accumulated_seconds"] = (
                        float(state["accumulated_seconds"] or 0.0) + elapsed
                    )

                if in_heat:
                    state["outside_since"] = None
                else:
                    if state["last_in_heat"]:
                        state["outside_since"] = now
                    outside_since = state["outside_since"]
                    if (
                        outside_since is not None
                        and now - float(outside_since) >= self.OUTSIDE_RESET_SECONDS
                    ):
                        state["accumulated_seconds"] = 0.0

                state["last_seen"] = now
                state["last_in_heat"] = in_heat

            return float(state["accumulated_seconds"] or 0.0)

    def purge_stale(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._state = {
                track_id: state
                for track_id, state in self._state.items()
                if now - float(state["last_seen"] or 0.0) < 60.0
            }


class CameraPersonTracker:
    """Associate person detections across frames from a single camera."""

    def __init__(self):
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
        timestamp: float | None = None,
    ) -> list[LocalTrack]:
        _ = timestamp  # Kept for deterministic tests and future timing-based tracking.
        observations = []
        for detection in detections:
            embedding, visual_quality = appearance_embedding(frame, detection["box"])
            observations.append({
                "detection": detection,
                "embedding": embedding,
                "quality": 0.8 * visual_quality + 0.2 * float(detection.get("conf", 0.0)),
                "point": self._point(detection["box"], width, height),
            })

        pairs: list[tuple[float, int, int]] = []
        diagonal = max(1.0, math.hypot(width, height))
        for local_id, track in self._tracks.items():
            old_center = (
                (track.box[0] + track.box[2]) / 2.0,
                (track.box[1] + track.box[3]) / 2.0,
            )
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
            track.point = observation["point"]
            track.missed = 0
            track.age_frames += 1
            track.update_embedding(observation["embedding"], observation["quality"])
            matched_tracks.add(local_id)
            matched_observations.add(observation_index)

        for local_id, track in list(self._tracks.items()):
            if local_id in matched_tracks:
                continue
            track.missed += 1
            if track.missed > TRACK_MAX_MISSED_FRAMES:
                del self._tracks[local_id]

        for index, observation in enumerate(observations):
            if index in matched_observations:
                continue
            local_id = self._next_local_id
            self._next_local_id += 1
            detection = observation["detection"]
            self._tracks[local_id] = LocalTrack(
                local_track_id=local_id,
                track_id=f"person-{local_id:06d}",
                box=detection["box"],
                confidence=float(detection.get("conf", 0.0)),
                embedding=observation["embedding"],
                quality=observation["quality"],
                point=observation["point"],
            )
            matched_tracks.add(local_id)

        return [
            self._tracks[local_id]
            for local_id in sorted(matched_tracks)
            if local_id in self._tracks
        ]

    def flush(self) -> None:
        self._tracks.clear()
