"""Anonymous demo account lifecycle and complete data cleanup."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from threading import RLock
from typing import Callable

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from .. import database
from ..config import (
    DEMO_CLOSE_GRACE_SECONDS,
    DEMO_IDLE_MINUTES,
    SUPABASE_CLIP_BUCKET,
    SUPABASE_DOCUMENT_BUCKET,
    SUPABASE_SNAPSHOT_BUCKET,
)
from ..time_utils import utc_now
from ..models import (
    AdminAuditLog,
    DocumentChunk,
    Event,
    EventEpisode,
    ExposureHourly,
    KnowledgeDocument,
    LoginSession,
    RiskPrediction,
    Site,
    User,
    Zone,
)

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
_purge_lock = RLock()


def _factory(session_factory: SessionFactory | None) -> SessionFactory:
    return session_factory or database.SessionLocal


def _safe_object_key(value: str | None) -> str | None:
    if not value or value.startswith(("/", "http://", "https://")):
        return None
    if ".." in PurePosixPath(value).parts:
        return None
    return value


def _stop_site_services(site_ids: list[int]) -> None:
    from .analysis_service import analysis_registry
    from .heat_service import heat_registry

    for site_id in site_ids:
        analysis_registry.stop_site(site_id)
        heat_registry.stop_site(site_id)


def _delete_storage_objects(objects: set[tuple[str, str]]) -> None:
    """Delete every referenced object or fail so database cleanup can retry later."""
    if not objects:
        return

    from .storage import get_storage

    storage = get_storage()
    failures: list[str] = []
    for bucket, key in sorted(objects):
        try:
            if not storage.delete(bucket, key):
                failures.append(f"{bucket}/{key}")
        except Exception as exc:
            logger.warning("Demo storage cleanup failed for %s/%s: %s", bucket, key, exc)
            failures.append(f"{bucket}/{key}")
    if failures:
        raise RuntimeError(
            "Supabase Storage 삭제를 확인하지 못했습니다: " + ", ".join(failures)
        )


def _collect_storage_objects(db: Session, site_ids: list[int]) -> set[tuple[str, str]]:
    objects: set[tuple[str, str]] = set()
    if not site_ids:
        return objects

    for key in db.scalars(
        select(KnowledgeDocument.storage_object_key).where(
            KnowledgeDocument.site_id.in_(site_ids)
        )
    ):
        safe_key = _safe_object_key(key)
        if safe_key:
            objects.add((SUPABASE_DOCUMENT_BUCKET, safe_key))

    for path in db.scalars(select(Event.snapshot_path).where(Event.site_id.in_(site_ids))):
        safe_key = _safe_object_key(path)
        if safe_key:
            objects.add((SUPABASE_SNAPSHOT_BUCKET, safe_key))

    for snapshot_path, clip_key in db.execute(
        select(EventEpisode.snapshot_path, EventEpisode.clip_object_key).where(
            EventEpisode.site_id.in_(site_ids)
        )
    ):
        safe_snapshot = _safe_object_key(snapshot_path)
        safe_clip = _safe_object_key(clip_key)
        if safe_snapshot:
            objects.add((SUPABASE_SNAPSHOT_BUCKET, safe_snapshot))
        if safe_clip:
            objects.add((SUPABASE_CLIP_BUCKET, safe_clip))
    return objects


def purge_demo_site(
    user_id: int,
    site_id: int,
    *,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Delete one demo site without leaving site-owned rows or objects behind."""
    make_session = _factory(session_factory)
    with _purge_lock:
        with make_session() as db:
            site = db.scalar(select(Site).where(
                Site.id == site_id,
                Site.user_id == user_id,
            ))
            user = db.get(User, user_id)
            if not site or not user or not user.is_ephemeral or user.status != "active":
                return False

        _stop_site_services([site_id])
        with make_session() as db:
            storage_objects = _collect_storage_objects(db, [site_id])
        _delete_storage_objects(storage_objects)

        with make_session() as db:
            site = db.scalar(select(Site).where(
                Site.id == site_id,
                Site.user_id == user_id,
            ))
            user = db.get(User, user_id)
            if not site or not user or not user.is_ephemeral:
                return False
            document_ids = list(db.scalars(
                select(KnowledgeDocument.id).where(KnowledgeDocument.site_id == site_id)
            ))
            if document_ids:
                db.execute(delete(DocumentChunk).where(or_(
                    DocumentChunk.site_id == site_id,
                    DocumentChunk.document_id.in_(document_ids),
                )))
            else:
                db.execute(delete(DocumentChunk).where(DocumentChunk.site_id == site_id))
            db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.site_id == site_id))
            db.execute(delete(RiskPrediction).where(RiskPrediction.site_id == site_id))
            db.execute(delete(ExposureHourly).where(ExposureHourly.site_id == site_id))
            db.execute(delete(EventEpisode).where(EventEpisode.site_id == site_id))
            db.execute(delete(Event).where(Event.site_id == site_id))
            db.execute(delete(Zone).where(Zone.site_id == site_id))
            if user.current_site_id == site_id:
                user.current_site_id = db.scalar(
                    select(Site.id).where(
                        Site.user_id == user_id,
                        Site.id != site_id,
                    ).order_by(Site.created_at, Site.id).limit(1)
                )
                db.flush()
            db.delete(site)
            db.commit()
        _stop_site_services([site_id])
        return True


def purge_demo_user(
    user_id: int,
    *,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Delete an ephemeral user only after all referenced Storage objects are gone.

    The account is first changed to ``deleting`` and its sessions are revoked. If
    Storage is unavailable, database rows remain intact and the periodic cleanup
    worker retries the operation.
    """
    make_session = _factory(session_factory)
    with _purge_lock:
        with make_session() as db:
            user = db.get(User, user_id)
            if not user or not user.is_ephemeral:
                return False
            site_ids = list(db.scalars(select(Site.id).where(Site.user_id == user_id)))
            user.status = "deleting"
            db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
            db.commit()

        _stop_site_services(site_ids)

        with make_session() as db:
            storage_objects = _collect_storage_objects(db, site_ids)
        _delete_storage_objects(storage_objects)

        with make_session() as db:
            user = db.get(User, user_id)
            if not user or not user.is_ephemeral:
                return False
            site_ids = list(db.scalars(select(Site.id).where(Site.user_id == user_id)))
            document_ids = list(db.scalars(
                select(KnowledgeDocument.id).where(KnowledgeDocument.site_id.in_(site_ids))
            ))

            if site_ids:
                if document_ids:
                    db.execute(delete(DocumentChunk).where(or_(
                        DocumentChunk.site_id.in_(site_ids),
                        DocumentChunk.document_id.in_(document_ids),
                    )))
                else:
                    db.execute(delete(DocumentChunk).where(DocumentChunk.site_id.in_(site_ids)))
                db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.site_id.in_(site_ids)))
                db.execute(delete(RiskPrediction).where(RiskPrediction.site_id.in_(site_ids)))
                db.execute(delete(ExposureHourly).where(ExposureHourly.site_id.in_(site_ids)))
                db.execute(delete(EventEpisode).where(EventEpisode.site_id.in_(site_ids)))
                db.execute(delete(Event).where(Event.site_id.in_(site_ids)))
                db.execute(delete(Zone).where(Zone.site_id.in_(site_ids)))

            db.execute(delete(AdminAuditLog).where(or_(
                AdminAuditLog.admin_user_id == user_id,
                AdminAuditLog.target_user_id == user_id,
            )))
            db.execute(update(User).where(User.id == user_id).values(current_site_id=None))
            db.execute(delete(Site).where(Site.user_id == user_id))
            db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
            db.execute(delete(User).where(User.id == user_id, User.is_ephemeral.is_(True)))
            db.commit()

        _stop_site_services(site_ids)
        return True


def purge_expired_demo_users(
    *,
    session_factory: SessionFactory | None = None,
) -> int:
    make_session = _factory(session_factory)
    now = utc_now()
    idle_cutoff = now - timedelta(minutes=DEMO_IDLE_MINUTES)
    close_cutoff = now - timedelta(seconds=DEMO_CLOSE_GRACE_SECONDS)
    with make_session() as db:
        user_ids = list(db.scalars(
            select(User.id).where(
                User.is_ephemeral.is_(True),
                or_(
                    User.status == "deleting",
                    User.expires_at.is_(None),
                    User.expires_at <= now,
                    User.last_seen_at.is_(None),
                    User.last_seen_at <= idle_cutoff,
                    User.cleanup_requested_at <= close_cutoff,
                ),
            )
        ))

    deleted_count = 0
    for user_id in user_ids:
        try:
            deleted_count += int(purge_demo_user(user_id, session_factory=make_session))
        except Exception:
            logger.exception("Expired demo cleanup failed for user_id=%s; will retry", user_id)
    return deleted_count


def touch_demo_user(
    user_id: int,
    *,
    session_factory: SessionFactory | None = None,
) -> bool:
    make_session = _factory(session_factory)
    with make_session() as db:
        result = db.execute(
            update(User)
            .where(
                User.id == user_id,
                User.is_ephemeral.is_(True),
                User.status == "active",
                User.expires_at > utc_now(),
            )
            .values(last_seen_at=utc_now())
        )
        db.commit()
        return bool(result.rowcount)


def count_active_demo_users(
    *,
    session_factory: SessionFactory | None = None,
) -> int:
    make_session = _factory(session_factory)
    now = utc_now()
    idle_cutoff = now - timedelta(minutes=DEMO_IDLE_MINUTES)
    with make_session() as db:
        return int(db.scalar(
            select(func.count(User.id)).where(
                User.is_ephemeral.is_(True),
                User.status == "active",
                User.expires_at > now,
                User.last_seen_at > idle_cutoff,
            )
        ) or 0)
