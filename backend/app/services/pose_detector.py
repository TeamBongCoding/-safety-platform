"""YOLO Pose model wrapper for keypoint extraction."""
from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np

from ..config import (
    MODEL_CONFIDENCE,
    MODEL_IMAGE_SIZE,
    POSE_ENABLED,
    POSE_KEYPOINT_CONF,
    POSE_MODEL_PATH,
    PROJECT_ROOT,
)

logger = logging.getLogger(__name__)

# COCO keypoint indices
KP_LEFT_SHOULDER = 5
KP_RIGHT_SHOULDER = 6
KP_LEFT_HIP = 11
KP_RIGHT_HIP = 12
KP_LEFT_KNEE = 13
KP_RIGHT_KNEE = 14
KP_LEFT_ANKLE = 15
KP_RIGHT_ANKLE = 16

# COCO skeleton pairs for debug drawing
SKELETON_PAIRS = [
    (5, 6), (5, 7), (6, 8), (7, 9), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (12, 14), (13, 15), (14, 16),
]


def extract_pose_features(kps: np.ndarray) -> Optional[dict]:
    """
    Extract behavior-relevant features from keypoints array (17, 3) [x, y, conf].
    Returns feature dict or None if hip keypoints are below confidence threshold.
    """
    def kp(idx: int):
        return float(kps[idx, 0]), float(kps[idx, 1]), float(kps[idx, 2])

    lh = kp(KP_LEFT_HIP)
    rh = kp(KP_RIGHT_HIP)
    ls = kp(KP_LEFT_SHOULDER)
    rs = kp(KP_RIGHT_SHOULDER)
    lk = kp(KP_LEFT_KNEE)
    rk = kp(KP_RIGHT_KNEE)

    # Hip center is required
    hip_pts = [(x, y) for x, y, c in (lh, rh) if c >= POSE_KEYPOINT_CONF]
    if not hip_pts:
        return None

    hip_cx = sum(x for x, y in hip_pts) / len(hip_pts)
    hip_cy = sum(y for x, y in hip_pts) / len(hip_pts)

    # Shoulder center (optional — used for body angle)
    sh_pts = [(x, y) for x, y, c in (ls, rs) if c >= POSE_KEYPOINT_CONF]
    shoulder_cx = sum(x for x, y in sh_pts) / len(sh_pts) if sh_pts else None
    shoulder_cy = sum(y for x, y in sh_pts) / len(sh_pts) if sh_pts else None

    # Knee mean y (optional — crouching indicator)
    kn_pts = [(x, y) for x, y, c in (lk, rk) if c >= POSE_KEYPOINT_CONF]
    knee_mean_y = sum(y for x, y in kn_pts) / len(kn_pts) if kn_pts else None

    hip_conf = sum(c for _, _, c in (lh, rh) if c >= POSE_KEYPOINT_CONF) / max(1, len(hip_pts))

    return {
        "hip_cx": hip_cx,
        "hip_cy": hip_cy,
        "shoulder_cx": shoulder_cx,
        "shoulder_cy": shoulder_cy,
        "knee_mean_y": knee_mean_y,
        "hip_conf": hip_conf,
        "raw_kps": kps,  # (17, 3) kept for debug skeleton drawing
    }


def _iou(a: list, b: list) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0.0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def match_pose_to_box(track_box: list, pose_detections: list) -> Optional[dict]:
    """Return best-matching pose features for a tracked person bbox, or None."""
    best_iou = 0.20
    best_features = None
    for bbox, kps in pose_detections:
        iou = _iou(track_box, bbox)
        if iou > best_iou:
            features = extract_pose_features(kps)
            if features is not None:
                best_iou = iou
                best_features = features
    return best_features


class PoseDetector:
    """Thread-safe YOLO Pose inference wrapper with lazy loading."""

    def __init__(self):
        self._lock = threading.Lock()
        self._model = None
        self._available = False
        if POSE_ENABLED:
            self._try_load()

    def _try_load(self):
        try:
            from ultralytics import YOLO
            import os
            path = POSE_MODEL_PATH
            if not os.path.isabs(path):
                candidate = PROJECT_ROOT / "backend" / path
                if candidate.exists():
                    path = str(candidate)
            self._model = YOLO(path)
            self._available = True
            logger.info("YOLO Pose model loaded: %s", path)
        except Exception as exc:
            logger.warning("YOLO Pose model load failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._available and POSE_ENABLED

    def detect(self, frame: np.ndarray) -> list[tuple[list, np.ndarray]]:
        """
        Detect poses in a frame.
        Returns list of (bbox [x1,y1,x2,y2], keypoints (17,3)) pairs.
        """
        if not self.available:
            return []
        with self._lock:
            try:
                results = self._model(
                    frame,
                    conf=MODEL_CONFIDENCE,
                    imgsz=MODEL_IMAGE_SIZE,
                    verbose=False,
                )[0]
                out = []
                if results.keypoints is not None and results.boxes is not None:
                    kps_data = results.keypoints.data.cpu().numpy()
                    boxes_data = results.boxes.xyxy.cpu().numpy()
                    for i in range(len(boxes_data)):
                        bbox = [float(v) for v in boxes_data[i]]
                        out.append((bbox, kps_data[i]))
                return out
            except Exception as exc:
                logger.debug("Pose detect error: %s", exc)
                return []

    def detect_batch(self, frames: list[np.ndarray]) -> list[list[tuple[list, np.ndarray]]]:
        """Run pose inference as a GPU batch for offline evaluation."""
        if not frames:
            return []
        if not self.available:
            return [[] for _ in frames]
        with self._lock:
            try:
                results = self._model(
                    frames,
                    conf=MODEL_CONFIDENCE,
                    imgsz=MODEL_IMAGE_SIZE,
                    verbose=False,
                    batch=len(frames),
                )
            except Exception as exc:
                logger.debug("Pose batch detect error: %s", exc)
                return [[] for _ in frames]

        batches = []
        for result in results:
            out = []
            if result.keypoints is not None and result.boxes is not None:
                kps_data = result.keypoints.data.cpu().numpy()
                boxes_data = result.boxes.xyxy.cpu().numpy()
                for index in range(len(boxes_data)):
                    bbox = [float(value) for value in boxes_data[index]]
                    out.append((bbox, kps_data[index]))
            batches.append(out)
        return batches



pose_detector = PoseDetector()
