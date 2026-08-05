"""바운딩박스 + 한글 상태 라벨 오버레이."""
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import FONT_PATH


FONT = ImageFont.truetype(FONT_PATH, 18)
LEVEL_COLORS = {"ok": (80, 220, 80), "warn": (0, 160, 255), "alert": (60, 60, 255)}


def draw_status(frame, box, helmet_on, hook_closed, zone_label, level):
    x1, y1, x2, y2 = map(int, box)
    color = LEVEL_COLORS[level]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

    lines = [
        f"헬멧 {'착용' if helmet_on else '미착용'}",
        f"고리 {'체결' if hook_closed else '미체결'}",
    ]
    if zone_label:
        lines.append(f"구역: {zone_label}")

    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(img)
    tx, ty = x2 + 6, y1
    for line in lines:
        w, h = d.textbbox((0, 0), line, font=FONT)[2:]
        d.rectangle([tx, ty, tx + w + 8, ty + h + 6], fill=(20, 20, 20))
        d.text((tx + 4, ty + 3), line, font=FONT, fill=tuple(reversed(color)))
        ty += h + 8
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def draw_zones(frame, zones, w, h):
    """저장된 구역 폴리곤을 색상별로 반투명 표시."""
    from .safety_rules import ZONE_TYPES
    overlay = frame.copy()
    for z in zones:
        pts = np.array([[int(x * w), int(y * h)] for x, y in z["polygon"]], np.int32)
        color = ZONE_TYPES[z["zone_type"]]["color"]
        cv2.fillPoly(overlay, [pts], color)
        cv2.polylines(frame, [pts], True, color, 2)
    return cv2.addWeighted(overlay, 0.25, frame, 0.75, 0)
