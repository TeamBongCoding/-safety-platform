from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Event
from ..schemas import EventOut

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(limit: int = 50, db: Session = Depends(get_db)):
    stmt = select(Event).order_by(Event.timestamp.desc()).limit(limit)
    return db.scalars(stmt).all()


@router.post("/{event_id}/resolve", response_model=EventOut)
def resolve_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    event.resolved = True
    db.commit()
    db.refresh(event)
    return event


@router.get("/stats/summary")
def stats_summary(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Event.id))) or 0
    by_type = db.execute(
        select(Event.event_type, func.count(Event.id)).group_by(Event.event_type)
    ).all()
    return {"total": total, "by_type": {t: c for t, c in by_type}}