# backend/app/schemas.py
from datetime import datetime
import math
from typing import Literal

from pydantic import BaseModel, Field
from pydantic import field_validator


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


class SiteOut(BaseModel):
    id: int
    name: str

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


class WorkerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    external_id: str | None = Field(default=None, max_length=100)
    rfid_tag: str | None = Field(default=None, max_length=100)


class WorkerOut(WorkerCreate):
    id: int
    active: bool

    class Config:
        from_attributes = True


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    source: str | None = Field(default=None, max_length=500)


class CameraOut(CameraCreate):
    id: int
    active: bool

    class Config:
        from_attributes = True


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    zone_type: Literal["no_entry", "fall_risk", "heavy_equip"] = "no_entry"
    risk_level: Literal["low", "medium", "high", "critical"] = "high"
    description: str = Field(default="", max_length=1000)
    precautions: str = Field(default="", max_length=1000)
    visible: bool = True
    camera_id: int | None = None
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
    snapshot_path: str | None
    confidence: float
    resolved: bool

    class Config:
        from_attributes = True
