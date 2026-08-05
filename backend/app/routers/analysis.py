import asyncio

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..services.analysis_service import analysis_service

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/status")
def analysis_status():
    return analysis_service.get_status()


@router.get("/stream")
async def analysis_stream():
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
