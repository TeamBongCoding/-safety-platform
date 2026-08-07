from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import require_current_site
from ..models import Event, Site
from ..schemas import EventOut

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def list_events(
    limit: int = 50,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 200)
    stmt = (
        select(Event)
        .where(Event.site_id == site.id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    )
    return db.scalars(stmt).all()


@router.post("/{event_id}/resolve", response_model=EventOut)
def resolve_event(
    event_id: int,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    event = db.scalar(
        select(Event).where(Event.id == event_id, Event.site_id == site.id)
    )
    if not event:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")
    event.resolved = True
    db.commit()
    db.refresh(event)
    return event


@router.get("/stats/summary")
def stats_summary(
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    total = db.scalar(
        select(func.count(Event.id)).where(Event.site_id == site.id)
    ) or 0
    by_type = db.execute(
        select(Event.event_type, func.count(Event.id))
        .where(Event.site_id == site.id)
        .group_by(Event.event_type)
    ).all()
    return {"total": total, "by_type": {t: c for t, c in by_type}}
