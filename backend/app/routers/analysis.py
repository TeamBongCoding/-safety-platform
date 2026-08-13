import asyncio

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from ..auth import require_current_site
from ..config import KMA_API_KEY
from ..models import Site
from ..services.analysis_service import analysis_registry
from ..services.heat_service import heat_registry

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def service_for_site(site: Site):
    heat_service = heat_registry.get(site.id, site.latitude, site.longitude, KMA_API_KEY)
    return analysis_registry.get(
        site.id,
        is_outdoor=site.is_outdoor,
        heat_service=heat_service,
    )


@router.get("/status")
def analysis_status(site: Site = Depends(require_current_site)):
    return service_for_site(site).get_status()


@router.get("/stream")
async def analysis_stream(site: Site = Depends(require_current_site)):
    analysis_service = service_for_site(site)

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


async def _current_frame(
    site: Site,
    original: bool = False,
    after: int | None = None,
) -> Response:
    analysis_service = service_for_site(site)
    for _ in range(50):
        jpeg, version = (
            analysis_service.get_original_frame()
            if original
            else analysis_service.get_frame()
        )
        if jpeg is not None and (after is None or version != after):
            return Response(
                content=jpeg,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "no-store",
                    "X-Frame-Version": str(version),
                },
            )
        await asyncio.sleep(0.03)
    raise HTTPException(status_code=503, detail="새 분석 프레임을 아직 사용할 수 없습니다.")


@router.get("/snapshot")
async def analysis_snapshot(site: Site = Depends(require_current_site)):
    return await _current_frame(site)


@router.get("/frame")
async def analysis_frame(
    after: int | None = None,
    site: Site = Depends(require_current_site),
):
    return await _current_frame(site, after=after)


@router.get("/original-frame")
async def analysis_original_frame(site: Site = Depends(require_current_site)):
    return await _current_frame(site, original=True)
