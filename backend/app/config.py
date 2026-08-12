"""Environment-backed application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv


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
MODEL_IMAGE_SIZE = int(os.getenv("MODEL_IMAGE_SIZE", "512"))
_default_font_path = (
    "C:/Windows/Fonts/malgun.ttf"
    if os.name == "nt"
    else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
)
FONT_PATH = os.getenv("FONT_PATH", _default_font_path)
TRACK_MAX_MISSED_FRAMES = int(os.getenv("TRACK_MAX_MISSED_FRAMES", "12"))
# ── Pose 행동 감지 ────────────────────────────────────────────────
POSE_ENABLED = os.getenv("POSE_ENABLED", "0") == "1"
POSE_MODEL_PATH = os.getenv("POSE_MODEL_PATH", "weights/yolo11n-pose.pt")
DEBUG_POSE = os.getenv("DEBUG_POSE", "0") == "1"
POSE_KEYPOINT_CONF = float(os.getenv("POSE_KEYPOINT_CONF", "0.4"))
POSE_INFER_EVERY = int(os.getenv("POSE_INFER_EVERY", "2"))
LIVE_INFER_EVERY = int(os.getenv("LIVE_INFER_EVERY", "4"))
LIVE_POSE_INFER_EVERY = int(os.getenv("LIVE_POSE_INFER_EVERY", "6"))
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
