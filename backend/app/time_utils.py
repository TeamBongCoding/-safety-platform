"""Korean Standard Time helpers for persisted and API-visible timestamps."""

from datetime import datetime, timedelta, timezone


KST = timezone(timedelta(hours=9), name="KST")


def kst_now() -> datetime:
    """Return an aware current timestamp in Korean Standard Time."""
    return datetime.now(KST)


def kst_now_naive() -> datetime:
    """Return KST without tzinfo for the existing naive SQLite columns."""
    return kst_now().replace(tzinfo=None)


def kst_today():
    return kst_now().date()


def kst_isoformat(value: datetime) -> str:
    """Serialize a stored event timestamp with an explicit +09:00 offset."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=KST)
    else:
        value = value.astimezone(KST)
    return value.isoformat()


def utc_naive_to_kst_isoformat(value: datetime) -> str:
    """Convert UTC-backed database timestamps to explicit Korean time.

    Risk/episode tables historically persist ``datetime.now()`` from the UTC
    server into timezone-naive columns. Treat those naive values as UTC before
    converting them, while still handling timezone-aware values correctly.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(KST).isoformat()
