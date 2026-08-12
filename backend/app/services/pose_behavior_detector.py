"""State-machine based heat behavior detection from YOLO Pose keypoints.

Per-(camera_id, local_track_id) history and state machine.
Detects: fall, stillness after fall, sudden sit, staggering.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..config import (
    FALL_BBOX_RATIO,
    FALL_BODY_ANGLE,
    FALL_DURATION_SEC,
    HEAT_BEHAVIOR_COOLDOWN_SEC,
    POSE_STATE_HOLD_SECONDS,
    STAGGER_DIRECTION_CHANGES,
    STAGGER_WINDOW_SEC,
    STILL_DURATION_SEC,
    STILL_MOVEMENT_THRESHOLD,
    SUDDEN_SIT_DROP_RATIO,
    SUDDEN_SIT_WINDOW_SEC,
)


class BehaviorState(str, Enum):
    NORMAL = "NORMAL"
    STAGGER = "STAGGER"
    SUDDEN_SIT = "SUDDEN_SIT"
    LYING = "LYING"
    FALL = "FALL"
    FALL_STILL = "FALL_STILL"


BEHAVIOR_RISK_SCORES: dict[BehaviorState, int] = {
    BehaviorState.NORMAL: 0,
    BehaviorState.STAGGER: 20,
    BehaviorState.SUDDEN_SIT: 25,
    BehaviorState.LYING: 40,
    BehaviorState.FALL: 60,
    BehaviorState.FALL_STILL: 90,
}

BEHAVIOR_LABELS: dict[BehaviorState, str] = {
    BehaviorState.NORMAL: "정상",
    BehaviorState.STAGGER: "휘청거림",
    BehaviorState.SUDDEN_SIT: "주저앉음",
    BehaviorState.LYING: "누워있음",
    BehaviorState.FALL: "쓰러짐",
    BehaviorState.FALL_STILL: "쓰러짐+정지",
}

# 폭염 상황일 때 event_type
BEHAVIOR_EVENT_TYPES: dict[BehaviorState, str] = {
    BehaviorState.STAGGER: "heat_stagger",
    BehaviorState.SUDDEN_SIT: "heat_sudden_sit",
    BehaviorState.LYING: "heat_lying",
    BehaviorState.FALL: "heat_fall",
    BehaviorState.FALL_STILL: "heat_fall_still",
}

# 일반 상황일 때 event_type (폭염 조건 무관)
BEHAVIOR_EVENT_TYPES_PLAIN: dict[BehaviorState, str] = {
    BehaviorState.STAGGER: "stagger",
    BehaviorState.SUDDEN_SIT: "sudden_sit",
    BehaviorState.LYING: "lying",
    BehaviorState.FALL: "fall",
    BehaviorState.FALL_STILL: "fall_still",
}


def behavior_event_type(state: BehaviorState, in_heat_zone: bool) -> str | None:
    """Preserve the API used by the current safety-event tests and callers."""
    event_types = BEHAVIOR_EVENT_TYPES if in_heat_zone else BEHAVIOR_EVENT_TYPES_PLAIN
    return event_types.get(state)

_STATE_CONFIDENCE: dict[BehaviorState, float] = {
    BehaviorState.NORMAL: 0.0,
    BehaviorState.STAGGER: 0.60,
    BehaviorState.SUDDEN_SIT: 0.65,
    BehaviorState.LYING: 0.75,
    BehaviorState.FALL: 0.85,
    BehaviorState.FALL_STILL: 0.95,
}


@dataclass
class PoseFrame:
    timestamp: float
    hip_cx: float
    hip_cy: float
    shoulder_cx: Optional[float]
    shoulder_cy: Optional[float]
    bbox_w: float
    bbox_h: float
    body_angle: Optional[float]  # degrees from horizontal: 90=upright, 0=lying flat
    bbox_ratio: float            # width / height


@dataclass
class BehaviorResult:
    state: BehaviorState = BehaviorState.NORMAL
    risk_score: int = 0
    confidence: float = 0.0
    label: str = "정상"
    event_type: Optional[str] = None
    debug: dict = field(default_factory=dict)


class _TrackHistory:
    """Per-track pose history and state machine."""

    def __init__(self):
        # ~10s at 30fps; timestamp-based windowing is used, not frame count
        self._history: deque[PoseFrame] = deque(maxlen=300)
        self._state: BehaviorState = BehaviorState.NORMAL
        self._fall_candidate_start: Optional[float] = None
        self._sudden_sit_last: float = 0.0

    def _recent(self, secs: float) -> list[PoseFrame]:
        if not self._history:
            return []
        cutoff = self._history[-1].timestamp - secs
        return [f for f in self._history if f.timestamp >= cutoff]

    # ── Condition detectors ───────────────────────────────────────────────

    def _is_fall_candidate(self, pf: PoseFrame) -> bool:
        """Bbox wider than tall OR torso angle close to horizontal."""
        ratio_ok = pf.bbox_ratio > FALL_BBOX_RATIO
        angle_ok = pf.body_angle is not None and pf.body_angle < FALL_BODY_ANGLE
        return ratio_ok or angle_ok

    def _is_lying_candidate(self, pf: PoseFrame) -> bool:
        """Recognize prone/push-up posture without calling it a confirmed fall."""
        angle_low = pf.body_angle is not None and pf.body_angle < 65.0
        low_profile = pf.bbox_ratio >= 0.75
        return pf.bbox_ratio >= 1.05 or (angle_low and low_profile)

    def _is_still(self, now: float) -> bool:
        """Hip center barely moved in the last STILL_DURATION_SEC (normalized)."""
        frames = self._recent(STILL_DURATION_SEC)
        if len(frames) < 3:
            return False
        mean_h = sum(f.bbox_h for f in frames) / len(frames)
        if mean_h <= 0:
            return False
        xs = [f.hip_cx for f in frames]
        ys = [f.hip_cy for f in frames]
        movement = math.hypot(
            (max(xs) - min(xs)) / mean_h,
            (max(ys) - min(ys)) / mean_h,
        )
        return movement < STILL_MOVEMENT_THRESHOLD

    def _is_sudden_sit(self) -> bool:
        """Hip dropped rapidly relative to bbox height within SUDDEN_SIT_WINDOW_SEC."""
        frames = self._recent(SUDDEN_SIT_WINDOW_SEC)
        if len(frames) < 2:
            return False
        oldest, newest = frames[0], frames[-1]
        if oldest.bbox_h <= 0:
            return False
        drop_ratio = (newest.hip_cy - oldest.hip_cy) / oldest.bbox_h
        if drop_ratio < SUDDEN_SIT_DROP_RATIO:
            return False
        # Exclude camera zoom-in (person just appears larger)
        if newest.bbox_h > oldest.bbox_h * 1.5:
            return False
        return True

    def _is_stagger(self) -> bool:
        """Lateral oscillation + lean variation over STAGGER_WINDOW_SEC (≥2/3 cues)."""
        frames = self._recent(STAGGER_WINDOW_SEC)
        if len(frames) < 5:
            return False

        score = 0

        # A: lateral direction reversals
        reversals = 0
        prev_dx: Optional[float] = None
        for i in range(1, len(frames)):
            dx = frames[i].hip_cx - frames[i - 1].hip_cx
            min_step = max(frames[i].bbox_w * 0.015, 1.0)
            if abs(dx) >= min_step:
                if prev_dx is not None and prev_dx * dx < 0:
                    reversals += 1
                prev_dx = dx
        if reversals >= STAGGER_DIRECTION_CHANGES:
            score += 1

        # B: body lean (angle) oscillation amplitude
        angles = [f.body_angle for f in frames if f.body_angle is not None]
        if len(angles) >= 4 and (max(angles) - min(angles)) > 20.0:
            score += 1

        # C: lateral displacement amplitude > 40% of bbox width
        mean_w = sum(f.bbox_w for f in frames) / len(frames)
        if mean_w > 0:
            xs = [f.hip_cx for f in frames]
            if (max(xs) - min(xs)) / mean_w > 0.40:
                score += 1

        return score >= 2

    # ── State machine ─────────────────────────────────────────────────────

    def update(self, pf: PoseFrame) -> BehaviorResult:
        self._history.append(pf)
        now = pf.timestamp

        # FALL candidate timer
        if self._is_fall_candidate(pf):
            if self._fall_candidate_start is None:
                self._fall_candidate_start = now
        else:
            self._fall_candidate_start = None

        fall_confirmed = (
            self._fall_candidate_start is not None
            and (now - self._fall_candidate_start) >= FALL_DURATION_SEC
        )

        # Priority: FALL_STILL > FALL > SUDDEN_SIT > STAGGER > NORMAL
        if fall_confirmed:
            self._state = BehaviorState.FALL_STILL if self._is_still(now) else BehaviorState.FALL
        elif self._is_lying_candidate(pf):
            self._state = BehaviorState.LYING
        else:
            if self._state in (BehaviorState.LYING, BehaviorState.FALL, BehaviorState.FALL_STILL):
                self._state = BehaviorState.NORMAL

            if self._is_sudden_sit():
                self._sudden_sit_last = now
                self._state = BehaviorState.SUDDEN_SIT
            elif self._state == BehaviorState.SUDDEN_SIT:
                if now - self._sudden_sit_last >= 3.0:
                    self._state = BehaviorState.NORMAL

            if self._state not in (BehaviorState.SUDDEN_SIT,):
                if self._is_stagger():
                    self._state = BehaviorState.STAGGER
                elif self._state == BehaviorState.STAGGER:
                    self._state = BehaviorState.NORMAL

        state = self._state
        fall_dur = round(now - self._fall_candidate_start, 2) if self._fall_candidate_start else 0.0

        return BehaviorResult(
            state=state,
            risk_score=BEHAVIOR_RISK_SCORES[state],
            confidence=_STATE_CONFIDENCE[state],
            label=BEHAVIOR_LABELS[state],
            event_type=BEHAVIOR_EVENT_TYPES.get(state),
            debug={
                "bbox_ratio": round(pf.bbox_ratio, 2),
                "body_angle": round(pf.body_angle, 1) if pf.body_angle is not None else None,
                "fall_candidate_dur": fall_dur,
                "state": state.value,
            },
        )


class PoseBehaviorDetector:
    """Manages per-(camera_id, local_track_id) behavior tracking and event cooldowns."""

    _CLEANUP_INTERVAL = 60.0
    _TRACK_TIMEOUT = 30.0

    def __init__(self):
        self._tracks: dict[tuple, _TrackHistory] = {}
        self._event_cooldowns: dict[tuple, float] = {}
        self._last_cleanup = 0.0

    def _cleanup_stale(self, now: float):
        if now - self._last_cleanup < self._CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        stale = [
            k for k, h in self._tracks.items()
            if h._history and now - h._history[-1].timestamp > self._TRACK_TIMEOUT
        ]
        for k in stale:
            del self._tracks[k]
            self._event_cooldowns.pop(k, None)

    def update(
        self,
        camera_id,
        local_track_id: int,
        pose_features: dict,
        bbox: list[float],
        timestamp: float,
    ) -> BehaviorResult:
        """Process one frame of pose data for a track and return behavior state."""
        self._cleanup_stale(timestamp)

        x1, y1, x2, y2 = bbox
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)

        scx = pose_features.get("shoulder_cx")
        scy = pose_features.get("shoulder_cy")
        hcx = pose_features["hip_cx"]
        hcy = pose_features["hip_cy"]

        # Body angle: degrees from horizontal
        # 90° = perfectly upright (standing), 0° = perfectly horizontal (lying)
        body_angle: Optional[float] = None
        if scx is not None and scy is not None:
            dx = abs(scx - hcx)
            dy = abs(scy - hcy)
            if dx > 1e-3 or dy > 1e-3:
                body_angle = math.degrees(math.atan2(dy, dx))

        pf = PoseFrame(
            timestamp=timestamp,
            hip_cx=hcx,
            hip_cy=hcy,
            shoulder_cx=scx,
            shoulder_cy=scy,
            bbox_w=bw,
            bbox_h=bh,
            body_angle=body_angle,
            bbox_ratio=bw / bh,
        )

        key = (camera_id, local_track_id)
        if key not in self._tracks:
            self._tracks[key] = _TrackHistory()
        return self._tracks[key].update(pf)

    def should_emit_event(
        self,
        camera_id,
        local_track_id: int,
        state: BehaviorState,
        now: float,
    ) -> bool:
        """Returns True if the event cooldown has passed for this track+state."""
        key = (camera_id, local_track_id, state.value)
        if now - self._event_cooldowns.get(key, 0.0) >= HEAT_BEHAVIOR_COOLDOWN_SEC:
            self._event_cooldowns[key] = now
            return True
        return False

    def current(self, camera_id, local_track_id: int, now: float) -> BehaviorResult | None:
        """Keep the latest abnormal state through a brief pose-model dropout."""
        history = self._tracks.get((camera_id, local_track_id))
        if history is None or not history._history:
            return None
        if now - history._history[-1].timestamp > POSE_STATE_HOLD_SECONDS:
            return None
        state = history._state
        if state == BehaviorState.NORMAL:
            return None
        return BehaviorResult(
            state=state,
            risk_score=BEHAVIOR_RISK_SCORES[state],
            confidence=_STATE_CONFIDENCE[state],
            label=BEHAVIOR_LABELS[state],
            event_type=BEHAVIOR_EVENT_TYPES.get(state),
            debug={"state": state.value, "cached": True},
        )

    def remove_track(self, camera_id, local_track_id: int):
        self._tracks.pop((camera_id, local_track_id), None)

    def reset(self) -> None:
        """Clear pose histories when an analysis source is stopped or switched."""
        self._tracks.clear()
        self._event_cooldowns.clear()
        self._last_cleanup = 0.0


pose_behavior_detector = PoseBehaviorDetector()
