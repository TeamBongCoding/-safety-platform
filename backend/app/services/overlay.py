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


def _load_font(size: int = 14):
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
            logger.info("폰트 로드 성공: %s (size=%d)", path, size)
            return font
        except (OSError, IOError):
            continue
    logger.warning("한글 폰트를 찾지 못했습니다. PIL 기본 폰트로 대체합니다.")
    return ImageFont.load_default()


FONT_SM = _load_font(13)   # ID 등 보조 정보
FONT_MD = _load_font(15)   # 상태 뱃지

LEVEL_COLORS = {"ok": (80, 220, 80), "warn": (0, 160, 255), "alert": (60, 60, 255)}

# 각 행(row)의 텍스트색·배경색 정의 (RGB)
_ROW_ID          = {"fg": (200, 200, 200), "bg": (25,  25,  25)}
_ROW_HELMET_OK   = {"fg": (90,  220, 90),  "bg": (12,  45,  12)}
_ROW_HELMET_NO   = {"fg": (100, 120, 255), "bg": (30,  15,  40)}
_ROW_ZONE        = {"fg": (160, 160, 160), "bg": (30,  30,  30)}
_ROW_HEAT        = {"fg": (255, 185, 50),  "bg": (60,  28,   0)}
_ROW_HEAT_NO     = {"fg": (120, 120, 120), "bg": (28,  28,  28)}

_PAD_X = 7
_PAD_Y = 4
_GAP   = 2   # 행 간 간격


def draw_status(
    frame,
    box,
    helmet_on,
    zone_label,
    level,
    global_person_id,
    local_track_id,
    in_heat_zone=False,
    heat_seconds=0.0,
):
    x1, y1, x2, y2 = map(int, box)
    box_color_bgr = LEVEL_COLORS[level]

    # 폭염구역이면 박스를 한 겹 더 — 주황 외곽선
    if in_heat_zone:
        cv2.rectangle(frame, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), (0, 140, 255), 2)
    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color_bgr, 2)

    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(img)

    # ── 행 정의 ──────────────────────────────────────────────
    short_id = global_person_id.replace("person-", "")
    rows = [
        (f"G·{short_id}  L·{local_track_id}", FONT_SM, _ROW_ID),
        ("안전모 착용" if helmet_on else "안전모 미착용", FONT_MD,
         _ROW_HELMET_OK if helmet_on else _ROW_HELMET_NO),
    ]
    if zone_label:
        rows.append((f"구역  {zone_label}", FONT_SM, _ROW_ZONE))
    if in_heat_zone:
        m, s = divmod(int(heat_seconds), 60)
        rows.append((f"폭염구역  연속 {m:02d}:{s:02d}", FONT_MD, _ROW_HEAT))
    else:
        rows.append(("폭염구역 아님", FONT_MD, _ROW_HEAT_NO))

    # ── 패널 너비 선계산 (정렬용) ─────────────────────────────
    panel_w = max(
        d.textbbox((0, 0), text, font=font)[2] + _PAD_X * 2
        for text, font, _ in rows
    )

    # ── 패널 우측에 행별 렌더링 ──────────────────────────────
    tx = x2 + 4
    ty = y1
    for text, font, style in rows:
        _, _, tw, th = d.textbbox((0, 0), text, font=font)
        row_h = th + _PAD_Y * 2
        d.rectangle([tx, ty, tx + panel_w, ty + row_h], fill=style["bg"])
        d.text((tx + _PAD_X, ty + _PAD_Y), text, font=font, fill=style["fg"])
        ty += row_h + _GAP

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
