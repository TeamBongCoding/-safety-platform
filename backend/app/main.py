import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .auth import user_from_token
from .config import CORS_ORIGINS, PROJECT_ROOT, SESSION_COOKIE_NAME
from .database import Base, SessionLocal, engine
from .migrations import migrate_legacy_schema
from .models import Camera, Site
from .routers import admin, analysis, auth, events, rankings, resources, sites, zones
from .services.analysis_service import analysis_registry

migrate_legacy_schema(engine)
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    analysis_registry.stop_all()


app = FastAPI(title="AI 안전관리 플랫폼", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(sites.router)
app.include_router(resources.router)
app.include_router(events.router)
app.include_router(rankings.router)
app.include_router(zones.router)
app.include_router(analysis.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    session_token = ws.cookies.get(SESSION_COOKIE_NAME)
    camera_id_raw = ws.query_params.get("camera_id")
    try:
        camera_id = int(camera_id_raw) if camera_id_raw else None
    except ValueError:
        await ws.close(code=4400, reason="올바른 카메라 ID가 필요합니다.")
        return
    with SessionLocal() as db:
        user = user_from_token(session_token, db)
        if not user:
            await ws.close(code=4401, reason="로그인이 필요합니다.")
            return
        if user.role == "platform_admin":
            await ws.close(code=4403, reason="서버 관리자 계정은 영상 분석을 사용하지 않습니다.")
            return
        site = db.scalar(
            select(Site).where(
                Site.id == user.current_site_id,
                Site.user_id == user.id,
            )
        )
        if not site:
            await ws.close(code=4403, reason="현장 접근 권한이 없습니다.")
            return
        site_id = site.id
        camera = None
        if camera_id is not None:
            camera = db.scalar(
                select(Camera).where(Camera.id == camera_id, Camera.site_id == site.id)
            )
            if not camera:
                await ws.close(code=4404, reason="카메라를 찾을 수 없습니다.")
                return

    analysis_service = analysis_registry.get(
        site_id,
        camera.id if camera else None,
        camera.source if camera else None,
    )
    await ws.accept()
    try:
        while True:
            with SessionLocal() as db:
                if not user_from_token(session_token, db):
                    await ws.close(code=4401, reason="세션이 만료되었거나 계정이 정지되었습니다.")
                    return
            summary = await asyncio.to_thread(analysis_service.get_summary)
            await ws.send_json(summary)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/camera-upload/{camera_id}")
async def camera_upload_endpoint(ws: WebSocket, camera_id: int):
    session_token = ws.cookies.get(SESSION_COOKIE_NAME)
    with SessionLocal() as db:
        user = user_from_token(session_token, db)
        if not user:
            await ws.close(code=4401, reason="로그인이 필요합니다.")
            return
        if user.role == "platform_admin":
            await ws.close(code=4403, reason="서버 관리자 계정은 영상 분석을 사용하지 않습니다.")
            return
        site = db.scalar(
            select(Site).where(Site.id == user.current_site_id, Site.user_id == user.id)
        )
        if not site:
            await ws.close(code=4403, reason="현장 접근 권한이 없습니다.")
            return
        camera = db.scalar(
            select(Camera).where(
                Camera.id == camera_id,
                Camera.site_id == site.id,
                Camera.active.is_(True),
                Camera.source == "browser",
            )
        )
        if not camera:
            await ws.close(code=4404, reason="브라우저 카메라를 찾을 수 없습니다.")
            return
        site_id = site.id

    service = analysis_registry.get(site_id, camera_id, "browser")
    if not service.attach_external_camera():
        await ws.close(code=4409, reason="이 카메라는 이미 다른 클라이언트에서 전송 중입니다.")
        return

    await ws.accept()
    last_auth_check = time.monotonic()
    try:
        while True:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=5)
            except TimeoutError:
                message = None
            if message is None or time.monotonic() - last_auth_check >= 5:
                with SessionLocal() as db:
                    current_user = user_from_token(session_token, db)
                    if not current_user or current_user.current_site_id != site_id:
                        await ws.close(code=4401, reason="세션 또는 현재 현장이 변경되었습니다.")
                        return
                last_auth_check = time.monotonic()
            if message is None:
                continue
            if message["type"] == "websocket.disconnect":
                break
            payload = message.get("bytes")
            if payload is None:
                continue
            if len(payload) > 1_500_000:
                await ws.close(code=1009, reason="카메라 프레임이 너무 큽니다.")
                return
            service.submit_jpeg(payload)
    except WebSocketDisconnect:
        pass
    finally:
        service.detach_external_camera()


frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
