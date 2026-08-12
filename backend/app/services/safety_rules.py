"""구역 유형과 안전모 상태를 결합한 안전 판정."""
from shapely.geometry import Point

# 단일 카메라에서 사용하는 안전 구역
ZONE_TYPES = {
    "no_entry":   {"label": "출입금지",   "color": (0, 0, 255)},    # 빨강
    "fall_risk":  {"label": "추락위험",   "color": (0, 140, 255)},
    "heavy_equip": {"label": "중장비",    "color": (0, 255, 255)},  # 노랑
    "work_area":  {"label": "작업구역",   "color": (255, 180, 0)},
}

APPROACH_MARGIN = 0.08   # 정규화 좌표 기준 접근 판정 거리

LEVEL_PRIORITY = {"ok": 0, "warn": 1, "alert": 2}

ZONE_EVENT_TYPES = {
    "no_entry": {
        "inside": ("zone_intrusion", "출입금지구역 침입"),
        "near": ("zone_approach", "출입금지구역 접근"),
    },
    "fall_risk": {
        "inside": ("fall_risk_entry", "추락위험 구역 진입"),
        "near": ("fall_risk_approach", "추락위험 구역 접근"),
    },
    "heavy_equip": {
        "inside": ("heavy_equipment_entry", "중장비 작업반경 진입"),
        "near": ("heavy_equipment_approach", "중장비 작업반경 접근"),
    },
}


def locate(foot_xy_norm, zone_polygons):
    """발 위치가 어느 구역 내부/접근 중인지 반환.
    zone_polygons: [{"id", "zone_type", "poly": shapely Polygon(정규화)}]
    return: (status, zone) status = "inside" | "near" | "outside"
    """
    p = Point(foot_xy_norm)
    safety_zones = [z for z in zone_polygons if z["zone_type"] in ZONE_TYPES]
    for z in safety_zones:
        if z["poly"].contains(p):
            return "inside", z
    for z in safety_zones:
        if z["poly"].distance(p) < APPROACH_MARGIN:
            return "near", z
    return "outside", None


def _zone_alert_level(zone_status: str, risk_level: str) -> str:
    """Map a configured risk level to the actual warning level."""
    if zone_status == "near":
        return "alert" if risk_level == "critical" else "warn"
    if zone_status == "inside":
        return "warn" if risk_level == "low" else "alert"
    return "ok"


def evaluate(zone_status, zone, helmet_on: bool | None):
    """Return the highest level and normalized safety violations.

    Each violation has a stable event type, a Korean display label, and its
    own level so persistence and UI code do not have to parse joined strings.
    """
    violations = []
    # General areas do not require a helmet. A helmet warning is meaningful
    # only when the worker is actually inside a configured safety zone.
    if helmet_on is False and zone is not None and zone_status == "inside":
        violations.append({
            "type": "no_helmet",
            "label": "안전모 미착용",
            "level": "alert",
            "zone_related": True,
        })

    # A work area defines where helmet rules apply, but entering it is not a
    # danger event by itself.
    if (
        zone is not None
        and zone_status in ("inside", "near")
        and zone["zone_type"] in ZONE_EVENT_TYPES
    ):
        event_type, label = ZONE_EVENT_TYPES[zone["zone_type"]][zone_status]
        violations.append({
            "type": event_type,
            "label": label,
            "level": _zone_alert_level(zone_status, zone.get("risk_level", "high")),
            "zone_related": True,
        })

    level = max(
        (violation["level"] for violation in violations),
        key=lambda value: LEVEL_PRIORITY[value],
        default="ok",
    )
    return level, violations
