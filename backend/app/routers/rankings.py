from datetime import datetime, time as datetime_time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..auth import require_user
from ..database import get_db
from ..models import Event, Site, User, Zone
from ..time_utils import kst_now, kst_today


router = APIRouter(prefix="/api/rankings", tags=["rankings"])


def _rank_rows(rows: list[dict]) -> list[dict]:
    """Assign the same dense rank to rows with the same warning count."""
    current_rank = 0
    previous_count = None
    for row in rows:
        warning_count = row["warning_count"]
        if warning_count != previous_count:
            current_rank += 1
            previous_count = warning_count
        row["rank"] = current_rank
    return rows


@router.get("/today")
def today_rankings(
    _user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    today = kst_today()
    start_of_day = datetime.combine(today, datetime_time.min)
    end_of_day = start_of_day + timedelta(days=1)
    helmet_warning_today = and_(
        Event.timestamp >= start_of_day,
        Event.timestamp < end_of_day,
        Event.event_type == "no_helmet",
        Event.zone_id.is_not(None),
    )

    company_count = func.count(Event.id).label("warning_count")
    company_rows = db.execute(
        select(
            User.company_name,
            func.count(func.distinct(Site.id)).label("site_count"),
            company_count,
        )
        .select_from(User)
        .outerjoin(Site, Site.user_id == User.id)
        .outerjoin(Event, and_(Event.site_id == Site.id, helmet_warning_today))
        .where(User.role == "user", User.status == "active")
        .group_by(User.company_name)
        .order_by(company_count.asc(), User.company_name.asc())
    ).all()
    companies = _rank_rows([
        {
            "company_name": company_name,
            "site_count": site_count,
            "warning_count": warning_count,
        }
        for company_name, site_count, warning_count in company_rows
    ])

    site_count = func.count(Event.id).label("warning_count")
    site_rows = db.execute(
        select(
            Site.id,
            Site.name,
            User.company_name,
            site_count,
        )
        .select_from(Site)
        .join(User, Site.user_id == User.id)
        .outerjoin(Event, and_(Event.site_id == Site.id, helmet_warning_today))
        .where(User.role == "user", User.status == "active")
        .group_by(Site.id, Site.name, User.company_name)
        .order_by(site_count.asc(), User.company_name.asc(), Site.name.asc())
    ).all()
    sites = _rank_rows([
        {
            "site_id": site_id,
            "site_name": site_name,
            "company_name": company_name,
            "warning_count": warning_count,
        }
        for site_id, site_name, company_name, warning_count in site_rows
    ])

    zone_count = func.count(Event.id).label("warning_count")
    zone_rows = db.execute(
        select(
            Zone.id,
            Zone.name,
            Site.id,
            Site.name,
            User.company_name,
            zone_count,
        )
        .select_from(Zone)
        .join(Site, Zone.site_id == Site.id)
        .join(User, Site.user_id == User.id)
        .outerjoin(Event, and_(Event.zone_id == Zone.id, helmet_warning_today))
        .where(User.role == "user", User.status == "active")
        .group_by(Zone.id, Zone.name, Site.id, Site.name, User.company_name)
        .order_by(
            zone_count.asc(),
            User.company_name.asc(),
            Site.name.asc(),
            Zone.name.asc(),
        )
    ).all()
    zones = _rank_rows([
        {
            "zone_id": zone_id,
            "zone_name": zone_name,
            "site_id": site_id,
            "site_name": site_name,
            "company_name": company_name,
            "warning_count": warning_count,
        }
        for zone_id, zone_name, site_id, site_name, company_name, warning_count in zone_rows
    ])

    return {
        "date": today.isoformat(),
        "generated_at": kst_now().isoformat(),
        "companies": companies,
        "sites": sites,
        "zones": zones,
    }
