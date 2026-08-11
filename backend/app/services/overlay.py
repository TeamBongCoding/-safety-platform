"""바운딩박스 + 한글 상태 라벨 오버레이."""
import logging
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import DEBUG_POSE, FONT_PATH, POSE_KEYPOINT_CONF

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
            return font
        except (OSError, IOError):
            continue
    logger.warning("한글 폰트를 찾지 못했습니다. PIL 기본 폰트로 대체합니다.")
    return ImageFont.load_default()


FONT_SM = _load_font(11)   # ID·보조 정보
FONT_MD = _load_font(12)   # 상태 뱃지

LEVEL_COLORS = {"ok": (80, 220, 80), "warn": (0, 160, 255), "alert": (60, 60, 255)}

# 행(row)별 텍스트색·배경색 (RGB)
_ROW_ID        = {"fg": (210, 210, 210), "bg": (20,  20,  20)}
_ROW_HELMET_OK = {"fg": (90,  220, 90),  "bg": (10,  40,  10)}
_ROW_HELMET_NO = {"fg": (120, 140, 255), "bg": (25,  10,  35)}
_ROW_ZONE      = {"fg": (160, 160, 160), "bg": (25,  25,  25)}
_ROW_HEAT      = {"fg": (255, 185, 50),  "bg": (55,  25,   0)}
_ROW_HEAT_NO   = {"fg": (100, 100, 100), "bg": (22,  22,  22)}
_ROW_BEHAVIOR_WARN  = {"fg": (255, 180, 50),  "bg": (55,  30,   0)}
_ROW_BEHAVIOR_ALERT = {"fg": (255,  60, 60),  "bg": (60,   0,   0)}

_PAD_X  = 4   # 좌우 여백
_PAD_Y  = 2   # 상하 여백
_GAP    = 1   # 행 간 간격
_ALPHA  = 195 # 배경 반투명도 (0=완전투명, 255=완전불투명)


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
    behavior_state=None,
    behavior_debug=None,
):
    x1, y1, x2, y2 = map(int, box)
    box_w = max(x2 - x1, 1)
    box_h = max(y2 - y1, 1)
    box_color_bgr = LEVEL_COLORS[level]

    # 폭염구역이면 주황 외곽선 추가
    if in_heat_zone:
        cv2.rectangle(frame, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), (0, 140, 255), 2)
    cv2.rectangle(frame, (x1, y1), (x2, y2), box_color_bgr, 2)

    # PIL RGBA 모드로 변환 (반투명 배경 지원)
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA))
    d = ImageDraw.Draw(img, "RGBA")

    # ── 행 정의 ──────────────────────────────────────────────────
    short_id = global_person_id.replace("person-", "")
    rows = [
        (f"G·{short_id} L·{local_track_id}", FONT_SM, _ROW_ID),
        ("안전모 착용" if helmet_on else "안전모 미착용", FONT_MD,
         _ROW_HELMET_OK if helmet_on else _ROW_HELMET_NO),
    ]
    if zone_label:
        rows.append((f"구역 {zone_label}", FONT_SM, _ROW_ZONE))
    if in_heat_zone:
        m, s = divmod(int(heat_seconds), 60)
        rows.append((f"폭염 연속 {m:02d}:{s:02d}", FONT_MD, _ROW_HEAT))
    else:
        rows.append(("폭염구역 아님", FONT_SM, _ROW_HEAT_NO))

    # 이상행동 감지 배지 (NORMAL 이 아닐 때만)
    if behavior_state is not None and behavior_state.value != "NORMAL":
        from .pose_behavior_detector import BEHAVIOR_LABELS, BehaviorState
        label = BEHAVIOR_LABELS.get(behavior_state, behavior_state.value)
        is_severe = behavior_state in (BehaviorState.FALL, BehaviorState.FALL_STILL)
        style = _ROW_BEHAVIOR_ALERT if is_severe else _ROW_BEHAVIOR_WARN
        rows.append((f"[행동] {label}", FONT_MD, style))

    # DEBUG_POSE: 진단 정보 추가 행
    if DEBUG_POSE and behavior_debug:
        ratio = behavior_debug.get("bbox_ratio")
        angle = behavior_debug.get("body_angle")
        debug_txt = f"R:{ratio:.2f} A:{angle:.0f}" if angle is not None else f"R:{ratio:.2f}"
        rows.append((debug_txt, FONT_SM, _ROW_ID))

    # ── 박스 내부 좌상단에 행 렌더링 ─────────────────────────────
    tx = x1 + 3
    ty = y1 + 3
    max_row_w = box_w - 6   # 박스 가로 여백

    for text, font, style in rows:
        tb = d.textbbox((0, 0), text, font=font)
        tw, th = tb[2], tb[3]
        row_w = min(tw + _PAD_X * 2, max_row_w)
        row_h = th + _PAD_Y * 2

        # 박스 하단을 벗어나면 중단
        if ty + row_h > y2 - 3:
            break

        # 반투명 배경
        d.rectangle(
            [tx, ty, tx + row_w, ty + row_h],
            fill=(*style["bg"], _ALPHA),
        )
        # 텍스트 (텍스트가 row_w 초과하면 잘릴 수 있으나 영상 가독성 우선)
        d.text(
            (tx + _PAD_X, ty + _PAD_Y),
            text,
            font=font,
            fill=(*style["fg"], 255),
        )
        ty += row_h + _GAP

    return cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGR)


def draw_pose_skeleton(frame, pose_detections: list) -> np.ndarray:
    """DEBUG_POSE=True 일 때 keypoint 와 skeleton 을 영상에 그린다."""
    if not DEBUG_POSE or not pose_detections:
        return frame
    from .pose_detector import SKELETON_PAIRS
    for _bbox, kps in pose_detections:
        # skeleton lines
        for i, j in SKELETON_PAIRS:
            if kps[i, 2] >= POSE_KEYPOINT_CONF and kps[j, 2] >= POSE_KEYPOINT_CONF:
                pt1 = (int(kps[i, 0]), int(kps[i, 1]))
                pt2 = (int(kps[j, 0]), int(kps[j, 1]))
                cv2.line(frame, pt1, pt2, (0, 230, 180), 1)
        # keypoint dots
        for k in range(kps.shape[0]):
            if kps[k, 2] >= POSE_KEYPOINT_CONF:
                cv2.circle(frame, (int(kps[k, 0]), int(kps[k, 1])), 3, (255, 220, 0), -1)
    return frame


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
