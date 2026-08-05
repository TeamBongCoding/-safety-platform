import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Zone
from ..schemas import ZoneCreate, ZoneOut

router = APIRouter(prefix="/api/zones", tags=["zones"])


@router.get("", response_model=list[ZoneOut])
def list_zones(db: Session = Depends(get_db)):
    zones = db.scalars(select(Zone)).all()
    return [
        ZoneOut(id=z.id, name=z.name, zone_type=z.zone_type,
                polygon=json.loads(z.polygon))
        for z in zones
    ]


@router.post("", response_model=ZoneOut)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)):
    zone = Zone(name=payload.name, zone_type=payload.zone_type,
                polygon=json.dumps(payload.polygon))
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return ZoneOut(id=zone.id, name=zone.name, zone_type=zone.zone_type,
                   polygon=payload.polygon)