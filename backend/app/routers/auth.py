from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..auth import end_session, hash_password, require_user, start_session, verify_password
from ..database import get_db
from ..models import Event, Site, User, Zone
from ..schemas import LoginRequest, ProfileUpdate, SessionOut, SignupRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def session_payload(user: User, db: Session) -> SessionOut:
    sites = db.scalars(
        select(Site).where(Site.user_id == user.id).order_by(Site.created_at, Site.id)
    ).all()
    current_site = next((site for site in sites if site.id == user.current_site_id), None)
    return SessionOut(user=user, sites=sites, current_site=current_site)


@router.post("/signup", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="올바른 이메일을 입력하세요.")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    is_first_user = (
        db.scalar(select(func.count(User.id)).where(User.role == "user")) or 0
    ) == 0
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        company_name=payload.company_name.strip(),
        manager_name=payload.manager_name.strip(),
        last_login_at=datetime.now(),
    )
    db.add(user)
    db.flush()
    site = Site(user_id=user.id, name=payload.site_name.strip())
    db.add(site)
    db.flush()
    user.current_site_id = site.id

    if is_first_user:
        db.execute(update(Zone).where(Zone.site_id.is_(None)).values(site_id=site.id))
        db.execute(update(Event).where(Event.site_id.is_(None)).values(site_id=site.id))

    db.commit()
    db.refresh(user)
    start_session(response, db, user)
    return session_payload(user, db)


@router.post("/login", response_model=SessionOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="정지된 계정입니다. 서버 관리자에게 문의하세요.")
    user.last_login_at = datetime.now()
    db.commit()
    start_session(response, db, user)
    return session_payload(user, db)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, request: Request, db: Session = Depends(get_db)):
    end_session(response, request, db)


@router.get("/me", response_model=SessionOut)
def me(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return session_payload(user, db)


@router.put("/profile", response_model=SessionOut)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    user.company_name = payload.company_name.strip()
    user.manager_name = payload.manager_name.strip()
    db.commit()
    db.refresh(user)
    return session_payload(user, db)
