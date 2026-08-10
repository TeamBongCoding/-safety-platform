"""프레임 내 사람 발밑 밝기로 양지/그늘 추정."""

from __future__ import annotations

import cv2
import numpy as np


def estimate_sun_shade(
    frame: np.ndarray,
    box: list,
    frame_avg: float,
    sun_threshold: float = 1.15,
    shade_threshold: float = 0.85,
) -> str:
    """박스 하단 20% 영역 밝기를 프레임 평균과 비교해 'sun'/'shade'/'unknown' 반환."""
    if frame_avg < 1.0:
        return 'unknown'
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    x1c = max(0, x1)
    y1c = max(0, y1)
    x2c = min(w, x2)
    y2c = min(h, y2)
    if x2c - x1c < 4 or y2c - y1c < 8:
        return 'unknown'
    # 프레임 경계에 잘린 박스는 신뢰도 낮음
    if x1c != x1 or x2c != x2 or y1c != y1 or y2c != y2:
        return 'unknown'
    roi_y1 = y1c + int((y2c - y1c) * 0.80)
    roi = frame[roi_y1:y2c, x1c:x2c]
    if roi.size == 0:
        return 'unknown'
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    ratio = float(gray_roi.mean()) / frame_avg
    if ratio > sun_threshold:
        return 'sun'
    if ratio < shade_threshold:
        return 'shade'
    return 'unknown'


def frame_avg_brightness(frame: np.ndarray) -> float:
    """프레임 전체 평균 밝기 (grayscale)."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.mean())
