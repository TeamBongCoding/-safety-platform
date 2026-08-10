"""폭염 위험 추정 API."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import require_current_site
from ..config import KMA_API_KEY
from ..models import Site
from ..services.heat_service import heat_registry

router = APIRouter(prefix="/api/heat", tags=["heat"])


def _svc(site: Site):
    return heat_registry.get(site.id, site.latitude, site.longitude, KMA_API_KEY)


@router.get("/status")
def get_heat_status(site: Site = Depends(require_current_site)):
    """현재 현장의 체감온도 상태를 반환한다."""
    status = _svc(site).get_status()
    return {
        "apparent_temp": status.apparent_temp,
        "level": status.level,
        "stale": status.stale,
        "demo_mode": status.demo_mode,
        "sun_threshold": status.sun_threshold,
        "shade_threshold": status.shade_threshold,
    }


class DemoPayload(BaseModel):
    apparent_temp: float | None = None


@router.patch("/demo")
def set_demo(payload: DemoPayload, site: Site = Depends(require_current_site)):
    """데모 체감온도를 설정하거나 해제한다 (apparent_temp=null 이면 해제)."""
    _svc(site).set_demo_temp(payload.apparent_temp)
    return {"ok": True}


class ThresholdPayload(BaseModel):
    sun_threshold: float = 1.15
    shade_threshold: float = 0.85


@router.patch("/thresholds")
def set_thresholds(payload: ThresholdPayload, site: Site = Depends(require_current_site)):
    """밝기 비율 임계값을 설정한다."""
    _svc(site).set_thresholds(payload.sun_threshold, payload.shade_threshold)
    return {"ok": True}
