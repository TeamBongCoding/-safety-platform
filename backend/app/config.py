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


ANALYSIS_ENABLED = os.getenv("ANALYSIS_ENABLED", "0") == "1"
VIDEO_SOURCE = os.getenv("VIDEO_SOURCE")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./safety.db")
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
FONT_PATH = os.getenv("FONT_PATH", "C:/Windows/Fonts/malgun.ttf")
TRACK_MAX_MISSED_FRAMES = int(os.getenv("TRACK_MAX_MISSED_FRAMES", "12"))
REID_MAX_TRANSITION_SECONDS = float(os.getenv("REID_MAX_TRANSITION_SECONDS", "30"))
REID_MIN_SIMILARITY = float(os.getenv("REID_MIN_SIMILARITY", "0.60"))
REID_SCORE_THRESHOLD = float(os.getenv("REID_SCORE_THRESHOLD", "0.68"))
REID_DEEP_WEIGHT = float(os.getenv("REID_DEEP_WEIGHT", "0.85"))
REID_IMAGE_SIZE = int(os.getenv("REID_IMAGE_SIZE", "192"))
REID_ENTRY_GRACE_FRAMES = int(os.getenv("REID_ENTRY_GRACE_FRAMES", "30"))
REID_ROI_MARGIN = float(os.getenv("REID_ROI_MARGIN", "0.025"))
REID_BACKEND = os.getenv("REID_BACKEND", "fastreid").lower()
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
