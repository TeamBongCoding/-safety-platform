from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    zone_type: Mapped[str] = mapped_column(String(20))   # no_entry | heavy_equipment
    polygon: Mapped[str] = mapped_column(Text)           # 0~1 정규화 좌표 JSON 문자열

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    event_type: Mapped[str] = mapped_column(String(30))  # no_helmet | zone_intrusion
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)