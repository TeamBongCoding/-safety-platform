"""바운딩박스 + 한글 상태 라벨 오버레이."""
import logging
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import FONT_PATH

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = [
    FONT_PATH,
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
]


def _load_font(size: int = 18):
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            logger.info("폰트 로드 성공: %s", path)
            return font
        except (OSError, IOError):
            continue
    logger.warning("한글 폰트를 찾지 못했습니다. PIL 기본 폰트로 대체합니다.")
    return ImageFont.load_default()


FONT = _load_font()
LEVEL_COLORS = {"ok": (80, 220, 80), "warn": (0, 160, 255), "alert": (60, 60, 255)}


def draw_status(frame, box, helmet_on, zone_label, level, global_person_id, local_track_id):
    x1, y1, x2, y2 = map(int, box)
    color = LEVEL_COLORS[level]
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

    lines = [
        f"Global {global_person_id} · Camera Local {local_track_id}",
        f"헬멧 {'착용' if helmet_on else '미착용'}",
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
