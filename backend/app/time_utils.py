"""UTC persistence and Korean Standard Time API serialization helpers."""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


UTC = timezone.utc
KST = timezone(timedelta(hours=9), name="KST")


def as_utc(value: datetime) -> datetime:
    """Normalize aware or legacy UTC-naive values to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def kst_now() -> datetime:
    return utc_now().astimezone(KST)


def kst_today() -> date:
    return kst_now().date()


def kst_day_start_utc(day: date | None = None) -> datetime:
    """Return KST midnight converted to the UTC instant used by the DB."""
    selected = day or kst_today()
    return datetime.combine(selected, time.min, tzinfo=KST).astimezone(UTC)


def utc_to_kst_isoformat(value: datetime) -> str:
    """Serialize a persisted UTC timestamp with an explicit +09:00 offset."""
    return as_utc(value).astimezone(KST).isoformat()


# Compatibility aliases now follow the single UTC persistence rule.
def kst_isoformat(value: datetime) -> str:
    return utc_to_kst_isoformat(value)


def utc_naive_to_kst_isoformat(value: datetime) -> str:
    return utc_to_kst_isoformat(value)


class UTCDateTime(TypeDecorator):
    """Return aware UTC datetimes on both PostgreSQL and SQLite.

    PostgreSQL stores ``TIMESTAMP WITH TIME ZONE``. SQLite has no timezone-aware
    type, so bind values are stored as UTC-naive and restored as aware UTC.
    Legacy naive Python inputs are interpreted as UTC during the transition.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        normalized = as_utc(value)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value, _dialect):
        if value is None:
            return None
        return as_utc(value)
