from datetime import datetime, timedelta
import secrets
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from ..auth import require_user, start_session, user_from_token
from ..config import (
    DEMO_IDLE_MINUTES,
    DEMO_MAX_ACTIVE_SESSIONS,
    DEMO_MAX_HOURS,
    SESSION_COOKIE_NAME,
)
from ..database import get_db
from ..models import Site, User
from ..schemas import SessionOut
from ..services.demo_accounts import purge_demo_user, purge_expired_demo_users
from ..time_utils import utc_now

router = APIRouter(prefix="/api/auth", tags=["auth"])
_admission_lock = Lock()


def _bound_session_factory(db: Session):
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


def _lock_demo_admission(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        # Serialize capacity checks across API workers without adding a lock table.
        db.execute(text("SELECT pg_advisory_xact_lock(724903118)"))


def session_payload(user: User, db: Session) -> SessionOut:
    sites = db.scalars(
        select(Site).where(Site.user_id == user.id).order_by(Site.created_at, Site.id)
    ).all()
    current_site = next((site for site in sites if site.id == user.current_site_id), None)
    return SessionOut(user=user, sites=sites, current_site=current_site)


@router.post("/demo", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def start_demo(request: Request, response: Response, db: Session = Depends(get_db)):
    existing = user_from_token(request.cookies.get(SESSION_COOKIE_NAME), db)
    if existing:
        return session_payload(existing, db)

    make_session = _bound_session_factory(db)
    db.rollback()
    purge_expired_demo_users(session_factory=make_session)
    db.rollback()

    now = utc_now()
    idle_cutoff = now - timedelta(minutes=DEMO_IDLE_MINUTES)
    with _admission_lock:
        _lock_demo_admission(db)
        active_count = int(db.scalar(
            select(func.count(User.id)).where(
                User.is_ephemeral.is_(True),
                User.status == "active",
                User.expires_at > now,
                User.last_seen_at > idle_cutoff,
            )
        ) or 0)
        if active_count >= DEMO_MAX_ACTIVE_SESSIONS:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="현재 데모 사용자가 많습니다. 잠시 후 다시 시도하세요.",
            )

        demo_id = uuid4().hex
        expires_at = now + timedelta(hours=DEMO_MAX_HOURS)
        user = User(
            email=f"demo-{demo_id}@internal.invalid",
            password_hash=secrets.token_urlsafe(48),
            company_name=f"데모 클라이언트 {demo_id[:6].upper()}",
            manager_name="임시 사용자",
            role="user",
            status="active",
            is_ephemeral=True,
            created_at=now,
            last_login_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
        db.add(user)
        db.flush()
        site = Site(user_id=user.id, name="데모 현장")
        db.add(site)
        db.flush()
        user.current_site_id = site.id
        db.commit()
        db.refresh(user)

    start_session(response, db, user, expires_at=expires_at)
    return session_payload(user, db)


@router.get("/me", response_model=SessionOut)
def me(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return session_payload(user, db)


@router.post("/heartbeat")
def heartbeat(user: User = Depends(require_user), db: Session = Depends(get_db)):
    user.last_seen_at = utc_now()
    db.commit()
    return {"active": True, "expires_at": user.expires_at}


@router.delete("/demo")
def end_demo(
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    user_id = user.id
    make_session = _bound_session_factory(db)
    db.rollback()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    try:
        deleted = purge_demo_user(user_id, session_factory=make_session)
    except Exception:
        # The account is already blocked and will be retried by the cleanup worker.
        response.status_code = status.HTTP_202_ACCEPTED
        return {"deleted": False, "cleanup_pending": True}
    return {"deleted": deleted, "cleanup_pending": False}
