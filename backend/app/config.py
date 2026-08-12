"""Environment-backed application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.getenv(name, default).split(",")
        if value.strip()
    )


def normalize_database_url(value: str) -> str:
    """Select psycopg v3 when a provider returns a generic PostgreSQL URL."""
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


ANALYSIS_ENABLED = os.getenv("ANALYSIS_ENABLED", "0") == "1"
KMA_API_KEY = os.getenv("KMA_API_KEY")
VIDEO_SOURCE = os.getenv("VIDEO_SOURCE")

# Supabase exposes standard PostgreSQL URLs, while this project installs the
# modern psycopg v3 driver. Make copied dashboard URLs work without requiring
# callers to remember SQLAlchemy's explicit driver suffix.
if os.getenv("DB_HOST"):
    database_password = os.getenv("DB_PASSWORD")
    if not database_password:
        raise ValueError("DB_PASSWORD is required when DB_HOST is configured")
    DATABASE_URL = URL.create(
        drivername="postgresql+psycopg",
        username=os.getenv("DB_USER", "postgres"),
        password=database_password,
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "postgres"),
    )
else:
    DATABASE_URL = normalize_database_url(
        os.getenv("DATABASE_URL", "sqlite:///./safety.db")
    )
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "safety_session")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
CORS_ORIGINS = _csv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)

HELMET_MODEL_PATH = os.getenv("HELMET_MODEL_PATH", "weights/best.pt")
PERSON_MODEL_PATH = os.getenv("PERSON_MODEL_PATH", "yolov8n.pt")
MODEL_CONFIDENCE = float(os.getenv("MODEL_CONFIDENCE", "0.4"))
_default_font_path = (
    "C:/Windows/Fonts/malgun.ttf"
    if os.name == "nt"
    else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
)
FONT_PATH = os.getenv("FONT_PATH", _default_font_path)
TRACK_MAX_MISSED_FRAMES = int(os.getenv("TRACK_MAX_MISSED_FRAMES", "12"))
TRACK_STATIONARY_MAX_MISSED_FRAMES = int(
    os.getenv("TRACK_STATIONARY_MAX_MISSED_FRAMES", "45")
)
TRACK_STATIONARY_DISTANCE = float(os.getenv("TRACK_STATIONARY_DISTANCE", "0.045"))
TRACK_OCCLUSION_IOU = float(os.getenv("TRACK_OCCLUSION_IOU", "0.20"))
TRACK_APPEARANCE_MATCH_THRESHOLD = float(
    os.getenv("TRACK_APPEARANCE_MATCH_THRESHOLD", "0.72")
)
REID_MAX_TRANSITION_SECONDS = float(os.getenv("REID_MAX_TRANSITION_SECONDS", "30"))
REID_MIN_SIMILARITY = float(os.getenv("REID_MIN_SIMILARITY", "0.60"))
REID_SCORE_THRESHOLD = float(os.getenv("REID_SCORE_THRESHOLD", "0.68"))
REID_DEEP_WEIGHT = float(os.getenv("REID_DEEP_WEIGHT", "0.85"))
REID_IMAGE_SIZE = int(os.getenv("REID_IMAGE_SIZE", "192"))
REID_ENTRY_GRACE_FRAMES = int(os.getenv("REID_ENTRY_GRACE_FRAMES", "30"))
REID_ROI_MARGIN = float(os.getenv("REID_ROI_MARGIN", "0.025"))
REID_BACKEND = os.getenv("REID_BACKEND", "fastreid").lower()
REID_OVERLAP_THRESHOLD = float(os.getenv("REID_OVERLAP_THRESHOLD", "0.80"))
REID_STRONG_MATCH_THRESHOLD = float(os.getenv("REID_STRONG_MATCH_THRESHOLD", "0.82"))
OVERLAP_TIME_TOLERANCE = float(os.getenv("OVERLAP_TIME_TOLERANCE", "2.0"))
OVERLAP_CONFIRM_FRAMES = int(os.getenv("OVERLAP_CONFIRM_FRAMES", "5"))
OVERLAP_SCORE_MARGIN = float(os.getenv("OVERLAP_SCORE_MARGIN", "0.04"))
EMBEDDING_HISTORY_SIZE = int(os.getenv("EMBEDDING_HISTORY_SIZE", "8"))
EMBEDDING_MIN_QUALITY = float(os.getenv("EMBEDDING_MIN_QUALITY", "0.30"))
HELMET_VOTE_WINDOW_SECONDS = float(os.getenv("HELMET_VOTE_WINDOW_SECONDS", "5.0"))
# ── Pose 행동 감지 ────────────────────────────────────────────────
POSE_ENABLED = os.getenv("POSE_ENABLED", "0") == "1"
POSE_MODEL_PATH = os.getenv("POSE_MODEL_PATH", "yolo11n-pose.pt")
DEBUG_POSE = os.getenv("DEBUG_POSE", "0") == "1"
POSE_MODEL_CONFIDENCE = float(os.getenv("POSE_MODEL_CONFIDENCE", "0.20"))
POSE_IMAGE_SIZE = int(os.getenv("POSE_IMAGE_SIZE", "1280"))
POSE_TRACK_HOLD_FRAMES = int(os.getenv("POSE_TRACK_HOLD_FRAMES", "6"))
POSE_TRACK_HOLD_SECONDS = float(os.getenv("POSE_TRACK_HOLD_SECONDS", "1.0"))
POSE_LOW_PROFILE_RATIO = float(os.getenv("POSE_LOW_PROFILE_RATIO", "0.65"))
POSE_LOW_PROFILE_HOLD_FRAMES = int(os.getenv("POSE_LOW_PROFILE_HOLD_FRAMES", "20"))
POSE_LOW_PROFILE_HOLD_SECONDS = float(os.getenv("POSE_LOW_PROFILE_HOLD_SECONDS", "3.0"))
POSE_STATE_HOLD_SECONDS = float(os.getenv("POSE_STATE_HOLD_SECONDS", "3.0"))
POSE_KEYPOINT_CONF = float(os.getenv("POSE_KEYPOINT_CONF", "0.4"))
POSE_INFER_EVERY = int(os.getenv("POSE_INFER_EVERY", "1"))
LIVE_INFER_EVERY = int(os.getenv("LIVE_INFER_EVERY", "1"))
LIVE_POSE_INFER_EVERY = int(os.getenv("LIVE_POSE_INFER_EVERY", "1"))
FALL_BBOX_RATIO = float(os.getenv("FALL_BBOX_RATIO", "1.2"))
FALL_BODY_ANGLE = float(os.getenv("FALL_BODY_ANGLE", "40.0"))
FALL_DURATION_SEC = float(os.getenv("FALL_DURATION_SEC", "1.0"))
STILL_DURATION_SEC = float(os.getenv("STILL_DURATION_SEC", "5.0"))
STILL_MOVEMENT_THRESHOLD = float(os.getenv("STILL_MOVEMENT_THRESHOLD", "0.03"))
SUDDEN_SIT_DROP_RATIO = float(os.getenv("SUDDEN_SIT_DROP_RATIO", "0.15"))
SUDDEN_SIT_WINDOW_SEC = float(os.getenv("SUDDEN_SIT_WINDOW_SEC", "1.0"))
STAGGER_WINDOW_SEC = float(os.getenv("STAGGER_WINDOW_SEC", "3.0"))
STAGGER_DIRECTION_CHANGES = int(os.getenv("STAGGER_DIRECTION_CHANGES", "4"))
HEAT_BEHAVIOR_COOLDOWN_SEC = float(os.getenv("HEAT_BEHAVIOR_COOLDOWN_SEC", "15.0"))
# ──────────────────────────────────────────────────────────────────

REID_DEVICE = os.getenv("REID_DEVICE", "auto").lower()
_fastreid_weights = Path(os.getenv(
    "FASTREID_WEIGHTS_PATH",
    str(PROJECT_ROOT / "backend" / "weights" / "market_bot_R50.pth"),
))
FASTREID_WEIGHTS_PATH = str(
    _fastreid_weights
    if _fastreid_weights.is_absolute()
    else (PROJECT_ROOT / _fastreid_weights).resolve()
)
