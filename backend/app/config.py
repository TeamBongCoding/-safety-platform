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
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "hackathon-device-key")
CORS_ORIGINS = _csv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)

HELMET_MODEL_PATH = os.getenv("HELMET_MODEL_PATH", "weights/best.pt")
PERSON_MODEL_PATH = os.getenv("PERSON_MODEL_PATH", "yolov8n.pt")
MODEL_CONFIDENCE = float(os.getenv("MODEL_CONFIDENCE", "0.4"))
FONT_PATH = os.getenv("FONT_PATH", "C:/Windows/Fonts/malgun.ttf")

APPROVED_RFID_TAGS = frozenset(
    _csv("APPROVED_RFID_TAGS", "A1B2C3D4,0F9E8D7C")
)
HARNESS_SIM_RFID_TAG = os.getenv("HARNESS_SIM_RFID_TAG", "A1B2C3D4")
HARNESS_SITE_ID = int(os.getenv("HARNESS_SITE_ID", "1"))
BACKEND_BASE_URL = os.getenv(
    "BACKEND_BASE_URL",
    "http://localhost:8000",
).rstrip("/")
