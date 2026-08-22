from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .time_utils import UTCDateTime, utc_now
from .config import DATABASE_URL, EMBEDDING_DIM


def _make_embedding_type():
    """pgvector Vector(dim) for PostgreSQL; JSON for SQLite/fallback."""
    if DATABASE_URL.startswith("sqlite"):
        return JSON
    try:
        from pgvector.sqlalchemy import Vector
        return Vector(EMBEDDING_DIM)
    except ImportError:
        return JSON


_EMBEDDING_TYPE = _make_embedding_type()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    company_name: Mapped[str] = mapped_column(String(100))
    manager_name: Mapped[str] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    current_site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id"), nullable=True
    )
    is_ephemeral: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    cleanup_requested_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Site(Base):
    __tablename__ = "sites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_outdoor: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(Integer, index=True)
    admin_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(50), index=True)
    target_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(50))
    zone_type: Mapped[str] = mapped_column(String(20))   # no_entry | heavy_equipment
    risk_level: Mapped[str] = mapped_column(String(20), default="high")
    description: Mapped[str] = mapped_column(Text, default="")
    precautions: Mapped[str] = mapped_column(Text, default="")
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    polygon: Mapped[str] = mapped_column(Text)           # 0~1 정규화 좌표 JSON 문자열
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    track_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    event_type: Mapped[str] = mapped_column(String(30))  # no_helmet | zone_intrusion | fall | heat_fall …
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class EventEpisode(Base):
    """연속 프레임의 동일 위험을 하나의 사건으로 집계한다."""
    __tablename__ = "event_episodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    camera_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    track_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    confidence_min: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_avg: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_max: Mapped[float] = mapped_column(Float, default=0.0)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    clip_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_version: Mapped[str] = mapped_column(String(50), default="1.0")
    rule_version: Mapped[str] = mapped_column(String(50), default="1.0")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ExposureHourly(Base):
    """시간별 작업자 노출량 집계 — 위험 발생률 정규화에 사용한다."""
    __tablename__ = "exposure_hourly"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    camera_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    observed_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    worker_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    max_concurrent_workers: Mapped[int] = mapped_column(Integer, default=0)
    average_workers: Mapped[float] = mapped_column(Float, default=0.0)
    frame_count: Mapped[int] = mapped_column(Integer, default=0)


class RiskPrediction(Base):
    """결정론적 Risk Engine이 계산한 위험 예측 결과."""
    __tablename__ = "risk_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    horizon: Mapped[str] = mapped_column(String(20))  # 24h | 7d
    risk_level: Mapped[str] = mapped_column(String(20))  # low | medium | high | critical
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_rate: Mapped[float] = mapped_column(Float, default=0.0)
    recent_rate: Mapped[float] = mapped_column(Float, default=0.0)
    change_percent: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(20), default="low")
    factors_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[str] = mapped_column(String(50), default="1.0")
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class KnowledgeDocument(Base):
    """RAG에 사용하는 안전 문서 메타데이터."""
    __tablename__ = "knowledge_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(255), default="")
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    effective_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    storage_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class DocumentChunk(Base):
    """문서 청크와 임베딩 벡터 — pgvector RAG 검색에 사용한다."""
    __tablename__ = "document_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    character_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # embedding: Vector(dim) on PostgreSQL, JSON list on SQLite
    embedding = Column(_EMBEDDING_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
