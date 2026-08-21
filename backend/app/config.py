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


KMA_API_KEY = os.getenv("KMA_API_KEY")

# Supabase exposes standard PostgreSQL URLs, while this project installs the
# modern psycopg v3 driver. Make copied dashboard URLs work without requiring
# callers to remember SQLAlchemy's explicit driver suffix.
DATABASE_URL = normalize_database_url(
    os.getenv("DATABASE_URL", "sqlite:///./safety.db")
)
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "safety_session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
DEMO_IDLE_MINUTES = max(1, int(os.getenv("DEMO_IDLE_MINUTES", "30")))
DEMO_MAX_HOURS = max(1, int(os.getenv("DEMO_MAX_HOURS", "2")))
DEMO_CLEANUP_INTERVAL_SECONDS = max(10, int(os.getenv("DEMO_CLEANUP_INTERVAL_SECONDS", "60")))
DEMO_MAX_ACTIVE_SESSIONS = max(1, int(os.getenv("DEMO_MAX_ACTIVE_SESSIONS", "5")))
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
REID_ENABLED = os.getenv("REID_ENABLED", "1") == "1"
_reid_weights = Path(
    os.getenv("FASTREID_WEIGHTS_PATH", "backend/weights/market_bot_R50.pth")
)
FASTREID_WEIGHTS_PATH = (
    _reid_weights if _reid_weights.is_absolute() else PROJECT_ROOT / _reid_weights
)
REID_DEVICE = os.getenv("REID_DEVICE", "auto")
REID_MATCH_THRESHOLD = float(os.getenv("REID_MATCH_THRESHOLD", "0.72"))
REID_GALLERY_SECONDS = float(os.getenv("REID_GALLERY_SECONDS", "120"))
TRACK_BUFFER_FRAMES = int(os.getenv("TRACK_BUFFER_FRAMES", "90"))
BLE_TAG_ACTIVE_SECONDS = float(os.getenv("BLE_TAG_ACTIVE_SECONDS", "20"))
# ── Pose 행동 감지 ────────────────────────────────────────────────
POSE_ENABLED = os.getenv("POSE_ENABLED", "0") == "1"
POSE_MODEL_PATH = os.getenv("POSE_MODEL_PATH", "weights/yolo11n-pose.pt")
DEBUG_POSE = os.getenv("DEBUG_POSE", "0") == "1"
POSE_KEYPOINT_CONF = float(os.getenv("POSE_KEYPOINT_CONF", "0.4"))
POSE_INFER_EVERY = int(os.getenv("POSE_INFER_EVERY", "2"))
LIVE_INFER_EVERY = int(os.getenv("LIVE_INFER_EVERY", "3"))
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
# ── Episode 집계 ──────────────────────────────────────────────────
EVENT_EPISODE_CLOSE_GAP_SEC = float(os.getenv("EVENT_EPISODE_CLOSE_GAP_SEC", "30"))
EVENT_EPISODE_MIN_DURATION_SEC = float(os.getenv("EVENT_EPISODE_MIN_DURATION_SEC", "2"))
EVENT_EPISODE_UPDATE_INTERVAL_SEC = float(os.getenv("EVENT_EPISODE_UPDATE_INTERVAL_SEC", "10"))
# ── Risk 추세 시간축 ────────────────────────────────────────────
RISK_WINDOW_MODE = os.getenv("RISK_WINDOW_MODE", "production").strip().lower()
if RISK_WINDOW_MODE not in {"production", "demo"}:
    RISK_WINDOW_MODE = "production"
RISK_REFRESH_SECONDS = max(1, int(os.getenv("RISK_REFRESH_SECONDS", "60")))
RISK_SHORT_WINDOW_MINUTES = max(1, int(os.getenv("RISK_SHORT_WINDOW_MINUTES", "1")))
RISK_LONG_WINDOW_MINUTES = max(
    RISK_SHORT_WINDOW_MINUTES,
    int(os.getenv("RISK_LONG_WINDOW_MINUTES", "5")),
)
MODEL_VERSION = os.getenv("MODEL_VERSION", "1.0")
RULE_VERSION = os.getenv("RULE_VERSION", "1.0")
# ── OpenAI LLM ───────────────────────────────────────────────────
OPENAI_ENABLED = os.getenv("OPENAI_ENABLED", "0") == "1"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# 해커톤에서 별도 OpenAI-compatible gateway를 제공할 때만 설정한다.
OPENAI_BASE_URL = (
    os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT_SEC = float(os.getenv("OPENAI_TIMEOUT_SEC", "60"))
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1800"))
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "1"))
# ── Supabase ──────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_DOCUMENT_BUCKET = os.getenv("SUPABASE_DOCUMENT_BUCKET", "safety-documents")
SUPABASE_SNAPSHOT_BUCKET = os.getenv("SUPABASE_SNAPSHOT_BUCKET", "event-snapshots")
SUPABASE_CLIP_BUCKET = os.getenv("SUPABASE_CLIP_BUCKET", "event-clips")
# ── RAG / Embedding ───────────────────────────────────────────────
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_BATCH_SIZE = max(1, min(2048, int(os.getenv("EMBEDDING_BATCH_SIZE", "128"))))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "8"))
RAG_THRESHOLD = float(os.getenv("RAG_THRESHOLD", "0.55"))
RAG_FALLBACK_THRESHOLD = float(os.getenv("RAG_FALLBACK_THRESHOLD", "0.35"))
# ──────────────────────────────────────────────────────────────────
