"""Detection, tracking, cross-camera Re-ID, zones and helmet status pipeline."""

import logging
from .brightness import estimate_sun_shade, frame_avg_brightness
from .helmet_logic import match_helmet_to_person
from .overlay import draw_status
from .safety_rules import ZONE_TYPES, evaluate, locate

_log = logging.getLogger(__name__)


def process_frame(
    frame,
    detections,
    zones,
    w,
    h,
    tracker,
    identity_manager,
    include_status=False,
    is_outdoor=False,
    heat_status=None,
    heat_exposure_tracker=None,
):
    persons = [d for d in detections if d["cls"] == "person"]
    helmets = [d["box"] for d in detections if d["cls"] == "helmet"]
    entry_zones = [zone for zone in zones if zone["zone_type"] == "camera_entry"]
    exit_zones = [zone for zone in zones if zone["zone_type"] == "camera_exit"]
    overlap_zones = [zone for zone in zones if zone["zone_type"] == "camera_overlap"]
    tracks = tracker.update(
        frame, persons, w, h, entry_zones, exit_zones, overlap_zones=overlap_zones
    )

    # HeatExposureTracker ID 병합 이벤트 처리 (Overlap Zone 매칭으로 발생)
    if heat_exposure_tracker is not None:
        for dropped_id, canonical_id in identity_manager.drain_pending_merges():
            heat_exposure_tracker.merge_ids(dropped_id, canonical_id)

    heat_level = heat_status.level if heat_status is not None else "inactive"
    sun_threshold = heat_status.sun_threshold if heat_status is not None else 1.15
    shade_threshold = heat_status.shade_threshold if heat_status is not None else 0.85
    do_shade = heat_level != "inactive"
    frame_avg = frame_avg_brightness(frame) if do_shade else None

    import time as _time
    _now = _time.monotonic()

    events = []
    workers = []
    for track in tracks:
        foot = track.point
        zone_status, zone = locate(foot, zones)
        helmet_on = match_helmet_to_person(track.box, helmets)

        # 헬멧 관측을 Global ID에 누적 (크로스카메라 집계용)
        identity_manager.update_helmet(
            track.global_person_id,
            tracker.camera_id,
            helmet_on,
            track.quality,
            _now,
        )
        # 복수 카메라 데이터가 있을 때만 크로스카메라 집계 결과 사용
        cross_cam_helmet = identity_manager.get_helmet_status(track.global_person_id)
        if cross_cam_helmet is not None:
            helmet_on = cross_cam_helmet

        level, reasons = evaluate(zone_status, zone, helmet_on)
        zone_label = ZONE_TYPES[zone["zone_type"]]["label"] if zone else None

        if do_shade and frame_avg is not None:
            instant = estimate_sun_shade(
                frame, track.box, frame_avg, sun_threshold, shade_threshold
            )
            track.update_shade(instant)
        shade = track.shade_status if do_shade else "unknown"
        rest_needed = do_shade and (heat_level == "severe" or shade == "sun")

        heat_seconds = 0.0
        if heat_exposure_tracker is not None and heat_level != "inactive":
            heat_seconds = heat_exposure_tracker.update(
                track.global_person_id, in_heat=rest_needed, now=_now
            )

        frame = draw_status(
            frame,
            track.box,
            helmet_on,
            zone_label,
            level,
            track.global_person_id,
            track.local_track_id,
            in_heat_zone=rest_needed,
            heat_seconds=heat_seconds,
        )

        worker = {
            "id": track.global_person_id,
            "global_person_id": track.global_person_id,
            "local_track_id": track.local_track_id,
            "helmet_on": helmet_on,
            "zone": zone_label or "일반구역",
            "level": level,
            "reasons": reasons,
            "confidence": round(track.confidence, 3),
            "image_quality": round(track.quality, 3),
            "reid_backend": track.reid_backend,
            "camera_transition": track.match_details,
            "reid_pending": bool(
                track.match_details is None and track.entry_grace_remaining > 0
            ),
            "shade_status": shade,
            "rest_needed": rest_needed,
            "in_overlap_zone": track.in_overlap_zone,
        }
        workers.append(worker)

        if level == "alert":
            events.append({
                "type": "+".join(reasons),
                "zone_id": zone["id"] if zone else None,
                "worker_id": track.global_person_id,
                "confidence": track.confidence,
            })

    if include_status:
        unique_person_count = len({w["global_person_id"] for w in workers})
        return frame, events, {
            "workers": workers,
            "worker_count": len(workers),
            "unique_person_count": unique_person_count,
            "no_helmet_count": sum(not worker["helmet_on"] for worker in workers),
            "transition_candidate_count": identity_manager.pending_count(),
            "entry_roi_count": len(entry_zones),
            "exit_roi_count": len(exit_zones),
            "overlap_roi_count": len(overlap_zones),
            "reid_backend": workers[0]["reid_backend"] if workers else None,
        }
    return frame, events
