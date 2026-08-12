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
    box_w = x2c - x1c
    box_h = y2c - y1c
    margin_x = max(4, int(box_w * 0.20))
    ground_h = max(6, int(box_h * 0.20))
    gx1 = max(0, x1c - margin_x)
    gx2 = min(w, x2c + margin_x)
    gy1 = min(h, y2c + 2)
    gy2 = min(h, gy1 + ground_h)
    roi = frame[gy1:gy2, gx1:gx2]
    if roi.size == 0:
        lower_y = y1c + int(box_h * 0.70)
        left = frame[lower_y:y2c, gx1:x1c]
        right = frame[lower_y:y2c, x2c:gx2]
        strips = [item for item in (left, right) if item.size]
        if strips:
            roi = np.concatenate(strips, axis=1)
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
