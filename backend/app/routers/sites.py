from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..auth import require_user
from ..database import get_db
from ..models import Site, User
from ..schemas import SessionOut, SiteCreate, SitePatch, SiteOut
from ..services.analysis_service import analysis_registry
from ..services.demo_accounts import purge_demo_site
from ..services.heat_service import heat_registry
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
    analysis_settings_changed = any((
        payload.is_outdoor is not None,
        payload.latitude is not None,
        payload.longitude is not None,
    ))
    if payload.name is not None:
        site.name = payload.name.strip()
    if payload.is_outdoor is not None:
        site.is_outdoor = payload.is_outdoor
    if payload.latitude is not None or payload.longitude is not None:
        site.latitude = payload.latitude
        site.longitude = payload.longitude
    db.commit()
    db.refresh(site)
    if analysis_settings_changed:
        analysis_registry.stop_site(site.id)
        heat_registry.stop_site(site.id)
    return site


@router.delete("/{site_id}", response_model=SessionOut)
def delete_site(
    site_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    user_id = user.id
    if not db.scalar(select(Site.id).where(Site.id == site_id, Site.user_id == user_id)):
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.")
    make_session = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        deleted = purge_demo_site(user_id, site_id, session_factory=make_session)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="업로드 자료 삭제를 확인하지 못해 현장을 보존했습니다. 잠시 후 다시 시도하세요.",
        ) from exc
    if not deleted:
        raise HTTPException(status_code=409, detail="현장을 삭제할 수 없습니다.")
    db.expire_all()
    refreshed_user = db.get(User, user_id)
    return session_payload(refreshed_user, db)


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
