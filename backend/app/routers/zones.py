import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shapely.geometry import Polygon
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import require_current_site
from ..models import Camera, Site, Zone
from ..schemas import ZoneCreate, ZoneOut, ZoneVisibility

router = APIRouter(prefix="/api/zones", tags=["zones"])


def serialize_zone(zone: Zone) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        camera_id=zone.camera_id,
        name=zone.name,
        zone_type=zone.zone_type,
        risk_level=zone.risk_level,
        description=zone.description or "",
        precautions=zone.precautions or "",
        visible=zone.visible,
        polygon=json.loads(zone.polygon),
        updated_at=zone.updated_at,
    )


def require_site_camera(camera_id: int | None, site: Site, db: Session) -> None:
    if camera_id is None:
        return
    camera = db.scalar(
        select(Camera).where(Camera.id == camera_id, Camera.site_id == site.id)
    )
    if not camera:
        raise HTTPException(status_code=404, detail="현재 현장의 카메라를 찾을 수 없습니다.")


def require_valid_polygon(points: list[list[float]]) -> None:
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area < 0.00001:
        raise HTTPException(status_code=422, detail="겹치지 않는 유효한 위험구역 모양을 지정하세요.")


def require_site_zone(zone_id: int, site: Site, db: Session) -> Zone:
    zone = db.scalar(select(Zone).where(Zone.id == zone_id, Zone.site_id == site.id))
    if not zone:
        raise HTTPException(status_code=404, detail="위험구역을 찾을 수 없습니다.")
    return zone


@router.get("", response_model=list[ZoneOut])
def list_zones(
    camera_id: int | None = Query(default=None),
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    require_site_camera(camera_id, site, db)
    camera_filter = Zone.camera_id == camera_id if camera_id is not None else Zone.camera_id.is_(None)
    zones = db.scalars(
        select(Zone).where(Zone.site_id == site.id, camera_filter).order_by(Zone.id)
    ).all()
    return [serialize_zone(zone) for zone in zones]


@router.post("", response_model=ZoneOut)
def create_zone(
    payload: ZoneCreate,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    require_site_camera(payload.camera_id, site, db)
    require_valid_polygon(payload.polygon)
    zone = Zone(
        site_id=site.id,
        camera_id=payload.camera_id,
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
    require_site_camera(payload.camera_id, site, db)
    if payload.camera_id != zone.camera_id:
        raise HTTPException(status_code=409, detail="위험구역의 카메라는 변경할 수 없습니다.")
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
    db.delete(zone)
    db.commit()
