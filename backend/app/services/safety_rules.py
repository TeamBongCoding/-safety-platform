"""구역 유형 × 헬멧 × 안전고리 복합 판정."""
from shapely.geometry import Point, Polygon

# 위험구역 3종
ZONE_TYPES = {
    "no_entry":   {"label": "출입금지",   "color": (0, 0, 255)},    # 빨강
    "fall_risk":  {"label": "추락위험",   "color": (0, 140, 255)},  # 주황: 안전고리 필수
    "heavy_equip": {"label": "중장비",    "color": (0, 255, 255)},  # 노랑
}

APPROACH_MARGIN = 0.08   # 정규화 좌표 기준 접근 판정 거리


def locate(foot_xy_norm, zone_polygons):
    """발 위치가 어느 구역 내부/접근 중인지 반환.
    zone_polygons: [{"id", "zone_type", "poly": shapely Polygon(정규화)}]
    return: (status, zone) status = "inside" | "near" | "outside"
    """
    p = Point(foot_xy_norm)
    for z in zone_polygons:
        if z["poly"].contains(p):
            return "inside", z
    for z in zone_polygons:
        if z["poly"].distance(p) < APPROACH_MARGIN:
            return "near", z
    return "outside", None


def evaluate(zone_status, zone, helmet_on: bool, hook_closed: bool):
    """복합 판정표 구현.
    return: (level, reasons)  level = "ok" | "warn" | "alert"
    """
    reasons = []

    if zone_status == "outside":
        return "ok", reasons          # 일반구역: 고리 미체결이어도 정상

    zt = zone["zone_type"]

    if zt == "no_entry" and zone_status == "inside":
        return "alert", ["출입금지구역 침입"]

    level = "ok"
    if not helmet_on:
        reasons.append("안전모 미착용")
        level = "alert" if zone_status == "inside" else "warn"

    if zt == "fall_risk" and not hook_closed:
        reasons.append("안전고리 미체결")
        if zone_status == "inside":
            level = "alert"           # 진입 + 미체결 → 긴급 알림
        elif level != "alert":
            level = "warn"            # 접근 + 미체결 → 진동·경고음

    if zt == "heavy_equip" and zone_status in ("inside", "near"):
        reasons.append("중장비 작업반경 접근")
        if level == "ok":
            level = "warn"

    return level, reasons