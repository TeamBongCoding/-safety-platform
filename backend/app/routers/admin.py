import json
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..auth import require_platform_admin
from ..database import get_db
from ..models import (
    AdminAuditLog,
    Camera,
    Event,
    LoginSession,
    Site,
    User,
    Worker,
    Zone,
)
from ..schemas import AdminDeleteRequest

router = APIRouter(prefix="/api/admin", tags=["admin"])


def add_audit(
    db: Session,
    admin: User,
    action: str,
    target: User | None = None,
    details: dict | None = None,
) -> None:
    db.add(AdminAuditLog(
        admin_user_id=admin.id,
        admin_email=admin.email,
        action=action,
        target_user_id=target.id if target else None,
        target_email=target.email if target else None,
        details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
    ))


def get_target_user(user_id: int, db: Session) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    return user


def user_summary(user: User, db: Session) -> dict:
    site_ids = list(db.scalars(select(Site.id).where(Site.user_id == user.id)))
    return {
        "id": user.id,
        "email": user.email,
        "company_name": user.company_name,
        "manager_name": user.manager_name,
        "role": user.role,
        "status": user.status,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "suspended_at": user.suspended_at,
        "site_count": len(site_ids),
        "worker_count": db.scalar(
            select(func.count(Worker.id)).where(Worker.site_id.in_(site_ids))
        ) or 0,
        "camera_count": db.scalar(
            select(func.count(Camera.id)).where(Camera.site_id.in_(site_ids))
        ) or 0,
        "event_count": db.scalar(
            select(func.count(Event.id)).where(Event.site_id.in_(site_ids))
        ) or 0,
        "unresolved_count": db.scalar(
            select(func.count(Event.id)).where(
                Event.site_id.in_(site_ids), Event.resolved.is_(False)
            )
        ) or 0,
    }


@router.get("/overview")
def overview(
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    del admin
    start_of_day = datetime.combine(date.today(), datetime_time.min)
    return {
        "account_count": db.scalar(
            select(func.count(User.id)).where(User.role == "user")
        ) or 0,
        "active_account_count": db.scalar(
            select(func.count(User.id)).where(
                User.role == "user", User.status == "active"
            )
        ) or 0,
        "site_count": db.scalar(select(func.count(Site.id))) or 0,
        "worker_count": db.scalar(select(func.count(Worker.id))) or 0,
        "camera_count": db.scalar(select(func.count(Camera.id))) or 0,
        "event_count": db.scalar(select(func.count(Event.id))) or 0,
        "events_today": db.scalar(
            select(func.count(Event.id)).where(Event.timestamp >= start_of_day)
        ) or 0,
        "unresolved_count": db.scalar(
            select(func.count(Event.id)).where(Event.resolved.is_(False))
        ) or 0,
        "database": "SQLite",
        "server_status": "online",
    }


@router.get("/users")
def list_users(
    q: str | None = None,
    account_status: str | None = None,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    del admin
    statement = select(User).order_by(User.created_at.desc(), User.id.desc())
    if q and q.strip():
        term = f"%{q.strip()}%"
        statement = statement.where(or_(
            User.email.ilike(term),
            User.company_name.ilike(term),
            User.manager_name.ilike(term),
        ))
    if account_status in {"active", "suspended"}:
        statement = statement.where(User.status == account_status)
    return [user_summary(user, db) for user in db.scalars(statement).all()]


@router.get("/users/{user_id}")
def user_detail(
    user_id: int,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    del admin
    user = get_target_user(user_id, db)
    sites = db.scalars(
        select(Site).where(Site.user_id == user.id).order_by(Site.id)
    ).all()
    site_details = []
    for site in sites:
        site_details.append({
            "id": site.id,
            "name": site.name,
            "created_at": site.created_at,
            "worker_count": db.scalar(
                select(func.count(Worker.id)).where(Worker.site_id == site.id)
            ) or 0,
            "camera_count": db.scalar(
                select(func.count(Camera.id)).where(Camera.site_id == site.id)
            ) or 0,
            "zone_count": db.scalar(
                select(func.count(Zone.id)).where(Zone.site_id == site.id)
            ) or 0,
            "event_count": db.scalar(
                select(func.count(Event.id)).where(Event.site_id == site.id)
            ) or 0,
        })
    return {**user_summary(user, db), "sites": site_details}


def ensure_manageable(admin: User, target: User, db: Session) -> None:
    if admin.id == target.id:
        raise HTTPException(status_code=409, detail="현재 로그인한 관리자 계정은 변경할 수 없습니다.")
    if target.role == "platform_admin":
        admin_count = db.scalar(
            select(func.count(User.id)).where(User.role == "platform_admin")
        ) or 0
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="마지막 서버 관리자 계정은 변경할 수 없습니다.")


@router.post("/users/{user_id}/suspend")
def suspend_user(
    user_id: int,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    target = get_target_user(user_id, db)
    ensure_manageable(admin, target, db)
    if target.status != "suspended":
        target.status = "suspended"
        target.suspended_at = datetime.now()
        db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
        add_audit(db, admin, "account_suspended", target, {"company_name": target.company_name})
        db.commit()
    return user_summary(target, db)


@router.post("/users/{user_id}/activate")
def activate_user(
    user_id: int,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    target = get_target_user(user_id, db)
    if target.status != "active":
        target.status = "active"
        target.suspended_at = None
        add_audit(db, admin, "account_activated", target, {"company_name": target.company_name})
        db.commit()
    return user_summary(target, db)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    payload: AdminDeleteRequest,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    target = get_target_user(user_id, db)
    ensure_manageable(admin, target, db)
    if payload.email.strip().lower() != target.email:
        raise HTTPException(status_code=422, detail="확인 이메일이 계정 이메일과 일치하지 않습니다.")

    site_ids = list(db.scalars(select(Site.id).where(Site.user_id == target.id)))
    details = user_summary(target, db)
    add_audit(db, admin, "account_deleted", target, details)
    db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
    db.execute(delete(Event).where(Event.site_id.in_(site_ids)))
    db.execute(delete(Zone).where(Zone.site_id.in_(site_ids)))
    db.execute(delete(Worker).where(Worker.site_id.in_(site_ids)))
    db.execute(delete(Camera).where(Camera.site_id.in_(site_ids)))
    db.execute(update(User).where(User.id == target.id).values(current_site_id=None))
    db.execute(delete(Site).where(Site.user_id == target.id))
    db.execute(delete(User).where(User.id == target.id))
    db.commit()


@router.get("/events")
def list_all_events(
    limit: int = 100,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    del admin
    limit = min(max(limit, 1), 500)
    rows = db.execute(
        select(Event, Site, User)
        .outerjoin(Site, Event.site_id == Site.id)
        .outerjoin(User, Site.user_id == User.id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    ).all()
    return [{
        "id": event.id,
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "confidence": event.confidence,
        "resolved": event.resolved,
        "site_id": site.id if site else None,
        "site_name": site.name if site else "미지정",
        "user_id": user.id if user else None,
        "company_name": user.company_name if user else "미지정",
    } for event, site, user in rows]


@router.delete("/events")
def delete_events(
    scope: Literal["today", "all"] = "today",
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    statement = delete(Event)
    if scope == "today":
        start_of_day = datetime.combine(date.today(), datetime_time.min)
        statement = statement.where(
            Event.timestamp >= start_of_day,
            Event.timestamp < start_of_day + timedelta(days=1),
        )

    result = db.execute(statement)
    deleted_count = result.rowcount or 0
    add_audit(
        db,
        admin,
        f"events_deleted_{scope}",
        details={"scope": scope, "deleted_count": deleted_count},
    )
    db.commit()
    return {"scope": scope, "deleted_count": deleted_count}


@router.get("/audit-logs")
def list_audit_logs(
    limit: int = 100,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    del admin
    limit = min(max(limit, 1), 500)
    logs = db.scalars(
        select(AdminAuditLog).order_by(AdminAuditLog.created_at.desc()).limit(limit)
    ).all()
    return [{
        "id": log.id,
        "admin_email": log.admin_email,
        "action": log.action,
        "target_user_id": log.target_user_id,
        "target_email": log.target_email,
        "details": json.loads(log.details) if log.details else None,
        "created_at": log.created_at,
    } for log in logs]
