from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site, require_user
from ..database import get_db
from ..models import Site, User
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
    if payload.latitude is not None or payload.longitude is not None:
        site.latitude = payload.latitude
        site.longitude = payload.longitude
    db.commit()
    db.refresh(site)
    return site


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
