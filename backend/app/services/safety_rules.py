"""구역 유형과 안전모 상태를 결합한 안전 판정."""
from shapely.geometry import Point

# 위험구역과 카메라 전환 ROI
ZONE_TYPES = {
    "no_entry":   {"label": "출입금지",   "color": (0, 0, 255)},    # 빨강
    "fall_risk":  {"label": "추락위험",   "color": (0, 140, 255)},
    "heavy_equip": {"label": "중장비",    "color": (0, 255, 255)},  # 노랑
    "camera_entry": {"label": "카메라 입구 ROI", "color": (255, 180, 0)},
    "camera_exit": {"label": "카메라 출구 ROI", "color": (255, 0, 180)},
    "camera_overlap": {"label": "카메라 중복 시야", "color": (180, 0, 255)},  # 보라
}

SAFETY_ZONE_TYPES = {"no_entry", "fall_risk", "heavy_equip"}

APPROACH_MARGIN = 0.08   # 정규화 좌표 기준 접근 판정 거리


def locate(foot_xy_norm, zone_polygons):
    """발 위치가 어느 구역 내부/접근 중인지 반환.
    zone_polygons: [{"id", "zone_type", "poly": shapely Polygon(정규화)}]
    return: (status, zone) status = "inside" | "near" | "outside"
    """
    p = Point(foot_xy_norm)
    safety_zones = [z for z in zone_polygons if z["zone_type"] in SAFETY_ZONE_TYPES]
    for z in safety_zones:
        if z["poly"].contains(p):
            return "inside", z
    for z in safety_zones:
        if z["poly"].distance(p) < APPROACH_MARGIN:
            return "near", z
    return "outside", None


def evaluate(zone_status, zone, helmet_on: bool):
    """복합 판정표 구현.
    return: (level, reasons)  level = "ok" | "warn" | "alert"
    """
    reasons = []

    if zone_status == "outside":
        return "ok", reasons

    zt = zone["zone_type"]

    if zt == "no_entry" and zone_status == "inside":
        return "alert", ["출입금지구역 침입"]

    level = "ok"
    if not helmet_on:
        reasons.append("안전모 미착용")
        level = "alert" if zone_status == "inside" else "warn"

    if zt == "fall_risk" and zone_status in ("inside", "near"):
        reasons.append("추락위험 구역 진입" if zone_status == "inside" else "추락위험 구역 접근")
        level = "alert" if zone_status == "inside" else "warn"

    if zt == "heavy_equip" and zone_status in ("inside", "near"):
        reasons.append("중장비 작업반경 접근")
        if level == "ok":
            level = "warn"

    return level, reasons
