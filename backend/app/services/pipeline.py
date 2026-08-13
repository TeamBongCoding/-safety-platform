"""Detection, local tracking, safety-zone, heat, helmet, and pose pipeline."""

import time

from .brightness import estimate_sun_shade, frame_avg_brightness
from .helmet_logic import helmet_state_for_person
from .overlay import draw_pose_skeleton, draw_status
from .pose_behavior_detector import (
    BehaviorState,
    behavior_event_type,
    pose_behavior_detector,
)
from .pose_detector import match_pose_to_box
from .safety_rules import ZONE_TYPES, evaluate, locate


def process_frame(
    frame,
    detections,
    zones,
    w,
    h,
    tracker,
    include_status=False,
    is_outdoor=False,
    heat_status=None,
    heat_exposure_tracker=None,
    pose_detections=None,
    timestamp=None,
    render_overlay=True,
):
    persons = [d for d in detections if d["cls"] == "person"]
    helmets = [d["box"] for d in detections if d["cls"] == "helmet"]
    uncovered_heads = [d["box"] for d in detections if d["cls"] == "no-helmet"]
    now = time.monotonic() if timestamp is None else timestamp
    tracks = tracker.update(frame, persons, w, h, timestamp=now)

    heat_level = heat_status.level if heat_status is not None else "inactive"
    sun_threshold = heat_status.sun_threshold if heat_status is not None else 1.15
    shade_threshold = heat_status.shade_threshold if heat_status is not None else 0.85
    do_shade = is_outdoor and heat_level != "inactive"
    frame_avg = frame_avg_brightness(frame) if do_shade else None

    events = []
    workers = []
    for track in tracks:
        zone_status, zone = locate(track.point, zones)
        helmet_on = helmet_state_for_person(track.box, helmets, uncovered_heads)
        level, violations = evaluate(zone_status, zone, helmet_on)
        helmet_violation = any(
            violation["type"] == "no_helmet" for violation in violations
        )
        reasons = [violation["label"] for violation in violations]
        zone_label = ZONE_TYPES[zone["zone_type"]]["label"] if zone else None

        if do_shade and frame_avg is not None:
            track.update_shade(
                estimate_sun_shade(frame, track.box, frame_avg, sun_threshold, shade_threshold)
            )
        shade = track.shade_status if do_shade else "unknown"
        in_heat_zone = do_shade and (heat_level == "severe" or shade == "sun")

        heat_seconds = 0.0
        if heat_exposure_tracker is not None:
            heat_seconds = heat_exposure_tracker.update(
                track.track_id,
                in_heat=in_heat_zone,
                now=now,
            )
        rest_needed = in_heat_zone and heat_seconds >= 10.0
        strong_rest_needed = in_heat_zone and heat_seconds >= 20.0

        behavior_result = None
        if pose_detections:
            pose_features = match_pose_to_box(track.box, pose_detections)
            if pose_features is not None:
                behavior_result = pose_behavior_detector.update(
                    track.local_track_id,
                    pose_features,
                    track.box,
                    now,
                )

        behavior_state = behavior_result.state if behavior_result else BehaviorState.NORMAL
        behavior_debug = behavior_result.debug if behavior_result else None

        if render_overlay:
            frame = draw_status(
                frame,
                track.box,
                helmet_on,
                zone_label,
                level,
                track.track_id,
                in_heat_zone=in_heat_zone,
                heat_seconds=heat_seconds,
                helmet_violation=helmet_violation,
                behavior_state=behavior_state,
                behavior_heat_related=(
                    in_heat_zone and behavior_state != BehaviorState.NORMAL
                ),
                behavior_debug=behavior_debug,
            )

        if behavior_result is not None and behavior_result.state != BehaviorState.NORMAL:
            event_type = behavior_event_type(behavior_result.state, in_heat_zone)
            if event_type and pose_behavior_detector.should_emit_event(
                track.local_track_id,
                behavior_result.state,
                now,
            ):
                events.append({
                    "type": event_type,
                    "zone_id": None,
                    "track_id": track.track_id,
                    "confidence": behavior_result.confidence,
                })

        for violation in violations:
            events.append({
                "type": violation["type"],
                "zone_id": zone["id"] if zone and violation["zone_related"] else None,
                "track_id": track.track_id,
                "confidence": track.confidence,
                "in_risk_zone": zone_status == "inside",
            })

        workers.append({
            "id": track.track_id,
            "track_id": track.track_id,
            "helmet_on": helmet_on,
            "zone": zone_label or "일반구역",
            "level": level,
            "reasons": reasons,
            "confidence": round(track.confidence, 3),
            "image_quality": round(track.quality, 3),
            "shade_status": shade,
            "in_heat_zone": in_heat_zone,
            "heat_seconds": round(heat_seconds, 1),
            "rest_needed": rest_needed,
            "strong_rest_needed": strong_rest_needed,
            "helmet_violation": helmet_violation,
            "behavior_state": behavior_state.value,
            "behavior_label": (
                f"폭염 {behavior_result.label}"
                if behavior_result and in_heat_zone and behavior_state != BehaviorState.NORMAL
                else behavior_result.label if behavior_result else "정상"
            ),
            "behavior_risk_score": behavior_result.risk_score if behavior_result else 0,
        })

    if render_overlay and pose_detections:
        frame = draw_pose_skeleton(frame, pose_detections)

    if include_status:
        return frame, events, {
            "workers": workers,
            "worker_count": len(workers),
            "no_helmet_count": sum(worker["helmet_violation"] for worker in workers),
        }
    return frame, events
