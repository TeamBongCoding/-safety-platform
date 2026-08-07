import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..database import get_db
from ..models import Camera, Site
from ..services.analysis_service import analysis_registry

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def service_for_camera(
    camera_id: int | None,
    site: Site,
    db: Session,
):
    if camera_id is None:
        return analysis_registry.get(site.id)
    camera = db.scalar(
        select(Camera).where(Camera.id == camera_id, Camera.site_id == site.id)
    )
    if not camera:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다.")
    return analysis_registry.get(site.id, camera.id, camera.source)


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
