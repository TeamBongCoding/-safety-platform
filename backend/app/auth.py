import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import COOKIE_SAMESITE, COOKIE_SECURE, SESSION_COOKIE_NAME, SESSION_DAYS
from .database import get_db
from .models import LoginSession, Site, User

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(response: Response, db: Session, user: User) -> None:
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(
        token_hash=_token_hash(token),
        user_id=user.id,
        expires_at=datetime.now() + timedelta(days=SESSION_DAYS),
    ))
    db.commit()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def end_session(response: Response, request: Request, db: Session) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = db.get(LoginSession, _token_hash(token))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite=COOKIE_SAMESITE,
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
    if not user or user.status != "active":
        db.delete(login_session)
        db.commit()
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(request.cookies.get(SESSION_COOKIE_NAME), db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return user


def require_platform_admin(user: User = Depends(require_user)) -> User:
    if user.role != "platform_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="서버 관리자 권한이 필요합니다.")
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
