from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site, require_user
from ..database import get_db
from ..models import Camera, Event, Site, User, Zone
from ..schemas import SessionOut, SiteCreate, SitePatch, SiteOut
from .auth import session_payload

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("", response_model=list[SiteOut])
def list_sites(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Site).where(Site.user_id == user.id).order_by(Site.created_at, Site.id)
    ).all()


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_site(
    payload: SiteCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    site = Site(
        user_id=user.id,
        name=payload.name.strip(),
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.add(site)
    db.flush()
    user.current_site_id = site.id
    db.commit()
    db.refresh(user)
    return session_payload(user, db)


@router.patch("/{site_id}", response_model=SiteOut)
def update_site(
    site_id: int,
    payload: SitePatch,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    site = db.scalar(select(Site).where(Site.id == site_id, Site.user_id == user.id))
    if not site:
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.")
    if payload.name is not None:
        site.name = payload.name.strip()
    if payload.is_outdoor is not None:
        site.is_outdoor = payload.is_outdoor
    if payload.latitude is not None or payload.longitude is not None:
        site.latitude = payload.latitude
        site.longitude = payload.longitude
    db.commit()
    db.refresh(site)
    return site


@router.delete("/{site_id}", response_model=SessionOut)
def delete_site(
    site_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    site = db.scalar(select(Site).where(Site.id == site_id, Site.user_id == user.id))
    if not site:
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.")

    all_sites = db.scalars(select(Site).where(Site.user_id == user.id)).all()
    if len(all_sites) <= 1:
        raise HTTPException(status_code=400, detail="마지막 현장은 삭제할 수 없습니다.")

    # 관련 데이터 삭제
    db.query(Event).filter(Event.site_id == site_id).delete()
    db.query(Zone).filter(Zone.site_id == site_id).delete()
    db.query(Camera).filter(Camera.site_id == site_id).delete()
    db.delete(site)

    # 삭제한 현장이 현재 현장이었으면 다른 현장으로 전환
    if user.current_site_id == site_id:
        next_site = next((s for s in all_sites if s.id != site_id), None)
        user.current_site_id = next_site.id if next_site else None

    db.commit()
    db.refresh(user)
    return session_payload(user, db)


@router.post("/{site_id}/select", response_model=SessionOut)
def select_site(
    site_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    site = db.scalar(select(Site).where(Site.id == site_id, Site.user_id == user.id))
    if not site:
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.")
    user.current_site_id = site.id
    db.commit()
    db.refresh(user)
    return session_payload(user, db)
