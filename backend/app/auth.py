import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import COOKIE_SECURE, SESSION_COOKIE_NAME
from .database import get_db
from .models import LoginSession, Site, User

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(
    response: Response,
    db: Session,
    user: User,
    *,
    expires_at: datetime,
) -> None:
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(
        token_hash=_token_hash(token),
        user_id=user.id,
        expires_at=expires_at,
    ))
    db.commit()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max(1, int((expires_at - datetime.now()).total_seconds())),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def user_from_token(token: str | None, db: Session) -> User | None:
    if not token:
        return None
    login_session = db.get(LoginSession, _token_hash(token))
    if not login_session:
        return None
    if login_session.expires_at <= datetime.now():
        db.delete(login_session)
        db.commit()
        return None
    user = db.get(User, login_session.user_id)
    now = datetime.now()
    if (
        not user
        or not user.is_ephemeral
        or user.status != "active"
        or user.expires_at is None
        or user.expires_at <= now
    ):
        db.delete(login_session)
        db.commit()
        return None
    if user.last_seen_at is None or user.last_seen_at < now - timedelta(minutes=1):
        user.last_seen_at = now
        db.commit()
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(request.cookies.get(SESSION_COOKIE_NAME), db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="데모 세션이 필요합니다.")
    return user


def require_current_site(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Site:
    if not user.current_site_id:
        raise HTTPException(status_code=409, detail="관리할 현장을 먼저 선택하세요.")
    site = db.scalar(
        select(Site).where(Site.id == user.current_site_id, Site.user_id == user.id)
    )
    if not site:
        raise HTTPException(status_code=404, detail="현장을 찾을 수 없습니다.")
    return site
