"""Detection, tracking, cross-camera Re-ID, zones and helmet status pipeline."""

from .helmet_logic import match_helmet_to_person
from .overlay import draw_status
from .safety_rules import ZONE_TYPES, evaluate, locate


def process_frame(
    frame,
    detections,
    zones,
    w,
    h,
    tracker,
    identity_manager,
    include_status=False,
):
    persons = [d for d in detections if d["cls"] == "person"]
    helmets = [d["box"] for d in detections if d["cls"] == "helmet"]
    entry_zones = [zone for zone in zones if zone["zone_type"] == "camera_entry"]
    exit_zones = [zone for zone in zones if zone["zone_type"] == "camera_exit"]
    tracks = tracker.update(frame, persons, w, h, entry_zones, exit_zones)

    events = []
    workers = []
    for track in tracks:
        foot = track.point
        zone_status, zone = locate(foot, zones)
        helmet_on = match_helmet_to_person(track.box, helmets)
        level, reasons = evaluate(zone_status, zone, helmet_on)
        zone_label = ZONE_TYPES[zone["zone_type"]]["label"] if zone else None
        frame = draw_status(
            frame,
            track.box,
            helmet_on,
            zone_label,
            level,
            track.global_person_id,
            track.local_track_id,
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
        return frame, events, {
            "workers": workers,
            "worker_count": len(workers),
            "no_helmet_count": sum(not worker["helmet_on"] for worker in workers),
            "transition_candidate_count": identity_manager.pending_count(),
            "entry_roi_count": len(entry_zones),
            "exit_roi_count": len(exit_zones),
            "reid_backend": workers[0]["reid_backend"] if workers else None,
        }
    return frame, events
