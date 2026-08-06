# app/services/pipeline.py
from .safety_rules import locate, evaluate, ZONE_TYPES
from .helmet_logic import match_helmet_to_person
from . import harness_store
from .overlay import draw_status
from ..state import latest_warn_devices


def process_frame(frame, detections, zones, w, h, site_id, include_status=False):
    """zones: [{"id","zone_type","polygon"(정규화),"poly"(shapely)}]
    return: (그려진 frame, [이벤트 dict])"""
    persons = [d for d in detections if d["cls"] == "person"]
    helmets = [d["box"] for d in detections if d["cls"] == "helmet"]
    hook = harness_store.get(site_id, "worker-1")["hook_closed"]

    events = []
    workers = []
    warn_any = False

    for worker_index, p in enumerate(persons, start=1):
        x1, y1, x2, y2 = p["box"]
        foot = (((x1 + x2) / 2) / w, y2 / h)          # 발 위치 정규화
        z_status, zone = locate(foot, zones)
        helmet_on = match_helmet_to_person(p["box"], helmets)
        level, reasons = evaluate(z_status, zone, helmet_on, hook)

        zone_label = ZONE_TYPES[zone["zone_type"]]["label"] if zone else None
        frame = draw_status(frame, p["box"], helmet_on, hook, zone_label, level)

        workers.append({
            "id": f"worker-{worker_index}",
            "helmet_on": helmet_on,
            "hook_closed": hook,
            "zone": zone_label or "일반구역",
            "level": level,
            "reasons": reasons,
            "confidence": round(p.get("conf", 0.0), 3),
        })

        if level != "ok":
            warn_any = True
        if level == "alert":
            events.append({"type": "+".join(reasons),
                           "zone_id": zone["id"] if zone else None,
                           "worker_id": f"worker-{worker_index}",
                           "confidence": p.get("conf", 0.0)})

    # ESP32 진동 지시 갱신
    if warn_any:
        latest_warn_devices.add((site_id, "worker-1"))
    else:
        latest_warn_devices.discard((site_id, "worker-1"))

    if include_status:
        status = {
            "workers": workers,
            "worker_count": len(workers),
            "no_helmet_count": sum(not worker["helmet_on"] for worker in workers),
            "unsecured_count": sum(not worker["hook_closed"] for worker in workers),
        }
        return frame, events, status

    return frame, events
