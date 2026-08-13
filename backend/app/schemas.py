# backend/app/schemas.py
from datetime import datetime
import math
from typing import Literal

from pydantic import BaseModel, Field
from pydantic import field_serializer, field_validator

from .time_utils import kst_isoformat


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    company_name: str = Field(min_length=1, max_length=100)
    manager_name: str = Field(min_length=1, max_length=50)
    site_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdate(BaseModel):
    company_name: str = Field(min_length=1, max_length=100)
    manager_name: str = Field(min_length=1, max_length=50)


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    latitude: float | None = None
    longitude: float | None = None


class SitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_outdoor: bool | None = None
    latitude: float | None = None
    longitude: float | None = None


class SiteOut(BaseModel):
    id: int
    name: str
    is_outdoor: bool = False
    latitude: float | None = None
    longitude: float | None = None

    class Config:
        from_attributes = True


class UserOut(BaseModel):
    id: int
    email: str
    company_name: str
    manager_name: str
    role: str
    status: str
    created_at: datetime
    last_login_at: datetime | None

    class Config:
        from_attributes = True


class SessionOut(BaseModel):
    user: UserOut
    sites: list[SiteOut]
    current_site: SiteOut | None


class AdminDeleteRequest(BaseModel):
    email: str


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    zone_type: Literal[
        "no_entry",
        "fall_risk",
        "heavy_equip",
        "work_area",
    ] = "no_entry"
    risk_level: Literal["low", "medium", "high", "critical"] = "high"
    description: str = Field(default="", max_length=1000)
    precautions: str = Field(default="", max_length=1000)
    visible: bool = True
    polygon: list[list[float]] = Field(min_length=3, max_length=50)

    @field_validator("name")
    @classmethod
    def validate_zone_name(cls, name):
        name = name.strip()
        if not name:
            raise ValueError("구역 이름을 입력하세요.")
        return name

    @field_validator("polygon")
    @classmethod
    def validate_normalized_polygon(cls, polygon):
        if any(
            len(point) != 2
            or not all(math.isfinite(value) and 0 <= value <= 1 for value in point)
            for point in polygon
        ):
            raise ValueError("모든 좌표는 0과 1 사이의 [x, y] 형식이어야 합니다.")
        if len({(point[0], point[1]) for point in polygon}) < 3:
            raise ValueError("서로 다른 점을 3개 이상 선택하세요.")
        return polygon


class ZoneVisibility(BaseModel):
    visible: bool


class ZoneOut(ZoneCreate):
    id: int
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class EventOut(BaseModel):
    id: int
    timestamp: datetime
    event_type: str
    zone_id: int | None
    track_id: str | None = None
    snapshot_path: str | None
    confidence: float
    resolved: bool

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return kst_isoformat(value)

    class Config:
        from_attributes = True


# ── EventEpisode ──────────────────────────────────────────────────────────────

class EpisodeOut(BaseModel):
    id: int
    site_id: int | None
    camera_id: str | None
    track_id: str | None
    event_type: str
    zone_id: int | None
    started_at: datetime
    ended_at: datetime | None
    duration_sec: float
    severity: str
    confidence_min: float
    confidence_avg: float
    confidence_max: float
    observation_count: int
    snapshot_path: str | None
    model_version: str
    rule_version: str
    resolved: bool
    resolution_note: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("started_at", "ended_at", "created_at", "updated_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return kst_isoformat(value) if value else None

    class Config:
        from_attributes = True


class EpisodeResolveRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


# ── Risk ──────────────────────────────────────────────────────────────────────

class RiskFactorOut(BaseModel):
    metric: str
    value: float
    description: str
    weight: float = 1.0


class RiskResultOut(BaseModel):
    event_type: str
    horizon: str
    risk_score: float
    risk_level: str
    baseline_rate: float
    recent_rate: float
    change_percent: float
    confidence_level: str
    factors: list[RiskFactorOut]
    limitations: list[str]
    generated_at: datetime
    site_id: int | None
    zone_id: int | None
    model_version: str

    @field_serializer("generated_at")
    def serialize_dt(self, value: datetime) -> str:
        return kst_isoformat(value)


class RiskOverviewOut(BaseModel):
    horizon: str
    results: list[RiskResultOut]
    overall_risk_level: str
    overall_risk_score: float
    generated_at: datetime

    @field_serializer("generated_at")
    def serialize_dt(self, value: datetime) -> str:
        return kst_isoformat(value)


class RiskReportOut(BaseModel):
    id: int
    event_type: str
    horizon: str
    risk_level: str
    risk_score: float
    change_percent: float
    confidence_level: str
    llm_report: dict | None
    generated_at: datetime

    @field_serializer("generated_at")
    def serialize_dt(self, value: datetime) -> str:
        return kst_isoformat(value)

    class Config:
        from_attributes = True


# ── Knowledge ─────────────────────────────────────────────────────────────────

class DocumentOut(BaseModel):
    id: int
    site_id: int | None
    title: str
    source: str
    version: str
    effective_date: datetime | None
    created_at: datetime
    chunk_count: int = 0

    @field_serializer("effective_date", "created_at")
    def serialize_dt(self, value: datetime | None) -> str | None:
        return kst_isoformat(value) if value else None

    class Config:
        from_attributes = True

class DocumentChunkOut(BaseModel):
    id: int
    document_id: int
    title: str
    section: str | None
    content: str