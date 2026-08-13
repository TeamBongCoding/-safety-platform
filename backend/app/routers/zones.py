import json

from fastapi import APIRouter, Depends, HTTPException, status
from shapely.geometry import Polygon
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..database import get_db
from ..models import Event, EventEpisode, RiskPrediction, Site, Zone
from ..schemas import ZoneCreate, ZoneOut, ZoneVisibility

router = APIRouter(prefix="/api/zones", tags=["zones"])

SAFETY_ZONE_TYPES = ("no_entry", "fall_risk", "heavy_equip", "work_area")


def serialize_zone(zone: Zone) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        name=zone.name,
        zone_type=zone.zone_type,
        risk_level=zone.risk_level,
        description=zone.description or "",
        precautions=zone.precautions or "",
        visible=zone.visible,
        polygon=json.loads(zone.polygon),
        updated_at=zone.updated_at,
    )


def require_valid_polygon(points: list[list[float]]) -> None:
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area < 0.00001:
        raise HTTPException(status_code=422, detail="겹치지 않는 유효한 위험구역 모양을 지정하세요.")


def require_site_zone(zone_id: int, site: Site, db: Session) -> Zone:
    zone = db.scalar(
        select(Zone).where(
            Zone.id == zone_id,
            Zone.site_id == site.id,
            Zone.zone_type.in_(SAFETY_ZONE_TYPES),
        )
    )
    if not zone:
        raise HTTPException(status_code=404, detail="위험구역을 찾을 수 없습니다.")
    return zone


@router.get("", response_model=list[ZoneOut])
def list_zones(
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    zones = db.scalars(
        select(Zone)
        .where(Zone.site_id == site.id, Zone.zone_type.in_(SAFETY_ZONE_TYPES))
        .order_by(Zone.id)
    ).all()
    return [serialize_zone(zone) for zone in zones]


@router.post("", response_model=ZoneOut)
def create_zone(
    payload: ZoneCreate,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    require_valid_polygon(payload.polygon)
    zone = Zone(
        site_id=site.id,
        name=payload.name.strip(),
        zone_type=payload.zone_type,
        risk_level=payload.risk_level,
        description=payload.description.strip(),
        precautions=payload.precautions.strip(),
        visible=payload.visible,
        polygon=json.dumps(payload.polygon),
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return serialize_zone(zone)


@router.put("/{zone_id}", response_model=ZoneOut)
def update_zone(
    zone_id: int,
    payload: ZoneCreate,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    zone = require_site_zone(zone_id, site, db)
    require_valid_polygon(payload.polygon)
    zone.name = payload.name.strip()
    zone.zone_type = payload.zone_type
    zone.risk_level = payload.risk_level
    zone.description = payload.description.strip()
    zone.precautions = payload.precautions.strip()
    zone.visible = payload.visible
    zone.polygon = json.dumps(payload.polygon)
    db.commit()
    db.refresh(zone)
    return serialize_zone(zone)


@router.patch("/{zone_id}/visibility", response_model=ZoneOut)
def update_zone_visibility(
    zone_id: int,
    payload: ZoneVisibility,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    zone = require_site_zone(zone_id, site, db)
    zone.visible = payload.visible
    db.commit()
    db.refresh(zone)
    return serialize_zone(zone)


@router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(
    zone_id: int,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    zone = require_site_zone(zone_id, site, db)
    for model in (Event, EventEpisode, RiskPrediction):
        db.execute(
            update(model)
            .where(model.zone_id == zone.id)
            .values(zone_id=None)
        )
    db.delete(zone)
    db.commit()
