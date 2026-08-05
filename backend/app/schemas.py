# backend/app/schemas.py
from datetime import datetime
from pydantic import BaseModel


class ZoneCreate(BaseModel):
    name: str
    zone_type: str
    polygon: list[list[float]]


class ZoneOut(ZoneCreate):
    id: int

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