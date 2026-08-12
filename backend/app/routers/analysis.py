import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..config import KMA_API_KEY
from ..database import get_db
from ..models import Camera, Site
from ..services.analysis_service import analysis_registry
from ..services.heat_service import heat_registry

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def service_for_camera(
    camera_id: int | None,
    site: Site,
    db: Session,
):
    heat_svc = heat_registry.get(site.id, site.latitude, site.longitude, KMA_API_KEY)
    if camera_id is None:
        return analysis_registry.get(site.id, is_outdoor=site.is_outdoor, heat_service=heat_svc)
    camera = db.scalar(
        select(Camera).where(Camera.id == camera_id, Camera.site_id == site.id)
    )
    if not camera:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다.")
    return analysis_registry.get(site.id, camera.id, camera.source, is_outdoor=camera.is_outdoor, heat_service=heat_svc)


@router.get("/status")
def analysis_status(
    camera_id: int | None = None,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    return service_for_camera(camera_id, site, db).get_status()


@router.get("/stream")
async def analysis_stream(
    camera_id: int | None = None,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    analysis_service = service_for_camera(camera_id, site, db)
    async def frames():
        last_version = -1
        while True:
            jpeg, version = analysis_service.get_frame()
            if jpeg is not None and version != last_version:
                last_version = version
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                    + jpeg
                    + b"\r\n"
                )
            await asyncio.sleep(0.03)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/snapshot")
async def analysis_snapshot(
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    """단일 JPEG 프레임 반환 — JupyterHub 폴링용"""
    analysis_service = service_for_camera(None, site, db)
    for _ in range(50):
        jpeg, _ = analysis_service.get_frame()
        if jpeg is not None:
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        await asyncio.sleep(0.03)
    raise HTTPException(status_code=503, detail="분석 프레임을 아직 사용할 수 없습니다.")


@router.get("/frame")
async def analysis_frame(
    camera_id: int | None = None,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    """단일 JPEG 프레임을 반환합니다. JupyterHub 등 MJPEG를 지원하지 않는 환경에서 폴링용으로 사용합니다."""
    analysis_service = service_for_camera(camera_id, site, db)
    for _ in range(50):  # 최대 1.5초 대기
        jpeg, _ = analysis_service.get_frame()
        if jpeg is not None:
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        await asyncio.sleep(0.03)
    raise HTTPException(status_code=503, detail="분석 프레임을 아직 사용할 수 없습니다.")
