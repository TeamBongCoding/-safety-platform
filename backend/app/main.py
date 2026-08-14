import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .auth import user_from_token
from .config import CORS_ORIGINS, KMA_API_KEY, PROJECT_ROOT, SESSION_COOKIE_NAME
from .database import Base, SessionLocal, engine
from .migrations import migrate_legacy_schema
from .models import Site
from .routers import admin, analysis, auth, events, heat, knowledge, rankings, risk, sites, zones
from .services.analysis_service import analysis_registry
from .services.heat_service import heat_registry

migrate_legacy_schema(engine)
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    analysis_registry.stop_all()
    heat_registry.stop_all()


app = FastAPI(title="AI 안전관리 플랫폼", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}
_CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "media-src 'self' blob:",
    "connect-src 'self' ws: wss:",
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
))


def _add_security_headers(response, is_https: bool):
    response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(self), microphone=(), geolocation=()"
    )
    if is_https:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def enforce_external_https_and_security_headers(request: Request, call_next):
    """외부 프록시 HTTP를 HTTPS로 전환하고 모든 HTTP 응답을 보호한다."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    forwarded_proto = forwarded_proto.split(",", 1)[0].strip().lower()
    host = (request.url.hostname or "").lower()
    is_external = host not in _LOCAL_HOSTS
    is_https = forwarded_proto == "https" or request.url.scheme == "https"

    if is_external and forwarded_proto == "http":
        target = request.url.replace(scheme="https")
        return _add_security_headers(
            RedirectResponse(str(target), status_code=307),
            is_https=True,
        )

    response = await call_next(request)
    return _add_security_headers(response, is_https=is_https)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(sites.router)
app.include_router(events.router)
app.include_router(rankings.router)
app.include_router(zones.router)
app.include_router(analysis.router)
app.include_router(heat.router)
app.include_router(risk.router)
app.include_router(knowledge.router)


@app.get("/health")
def health():
    from .config import DATABASE_URL, OPENAI_ENABLED
    return {
        "status": "ok",
        "db": "sqlite" if DATABASE_URL.startswith("sqlite") else "postgresql",
        "llm_enabled": OPENAI_ENABLED,
        "llm_provider": "openai" if OPENAI_ENABLED else None,
    }


@app.websocket("/ws/camera-upload")
async def ws_camera_upload(ws: WebSocket):
    """브라우저에서 캡처한 JPEG 프레임을 수신하여 영상 분석 서비스에 주입합니다."""
    session_token = ws.cookies.get(SESSION_COOKIE_NAME)
    with SessionLocal() as db:
        user = user_from_token(session_token, db)
        if not user:
            await ws.close(code=4401, reason="로그인이 필요합니다.")
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
        is_outdoor = site.is_outdoor
        site_lat = site.latitude
        site_lon = site.longitude

    heat_svc = heat_registry.get(site_id, site_lat, site_lon, KMA_API_KEY)
    # 기존 파일 기반 서비스를 중지하고 외부 카메라 서비스로 전환
    analysis_registry.stop_site(site_id)
    service = analysis_registry.get(site_id, source="browser", is_outdoor=is_outdoor, heat_service=heat_svc)

    await ws.accept()
    if not service.attach_external_camera():
        await ws.close(code=4409, reason="이미 다른 카메라가 연결되어 있습니다.")
        return

    last_auth_check = time.monotonic()
    try:
        while True:
            data = await ws.receive_bytes()
            if len(data) > 1_500_000:
                await ws.close(code=1009, reason="카메라 프레임이 너무 큽니다.")
                return
            now = time.monotonic()
            if now - last_auth_check >= 5:
                with SessionLocal() as db:
                    current_user = user_from_token(session_token, db)
                    if not current_user or current_user.current_site_id != site_id:
                        await ws.close(code=4401, reason="세션 또는 현재 현장이 변경되었습니다.")
                        return
                last_auth_check = now
            service.submit_jpeg(data)
    except WebSocketDisconnect:
        pass
    finally:
        service.detach_external_camera()
        analysis_registry.stop_site(site_id)
        # 현장이 여전히 존재하고 현재 선택된 경우에만 파일 기반 분석으로 복귀한다.
        with SessionLocal() as db:
            current_user = user_from_token(session_token, db)
            site_still_active = bool(
                current_user
                and current_user.current_site_id == site_id
                and db.scalar(select(Site.id).where(Site.id == site_id))
            )
        if site_still_active:
            heat_svc2 = heat_registry.get(site_id, site_lat, site_lon, KMA_API_KEY)
            analysis_registry.get(site_id, is_outdoor=is_outdoor, heat_service=heat_svc2)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
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
            select(Site).where(
                Site.id == user.current_site_id,
                Site.user_id == user.id,
            )
        )
        if not site:
            await ws.close(code=4403, reason="현장 접근 권한이 없습니다.")
            return
        site_id = site.id
        is_outdoor = site.is_outdoor
        site_lat = site.latitude
        site_lon = site.longitude

    heat_svc = heat_registry.get(site_id, site_lat, site_lon, KMA_API_KEY)
    analysis_registry.get(
        site_id,
        is_outdoor=is_outdoor,
        heat_service=heat_svc,
    )
    await ws.accept()
    try:
        while True:
            with SessionLocal() as db:
                current_user = user_from_token(session_token, db)
                if not current_user or current_user.current_site_id != site_id:
                    await ws.close(code=4401, reason="세션이 만료되었거나 계정이 정지되었습니다.")
                    return
            service = analysis_registry.current(site_id)
            if service is None:
                service = analysis_registry.get(site_id, is_outdoor=is_outdoor, heat_service=heat_svc)
            try:
                summary = await asyncio.to_thread(service.get_summary)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("get_summary 오류: %s", exc)
                await asyncio.sleep(1)
                continue
            try:
                await ws.send_json(summary)
            except Exception:
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


frontend_dist = PROJECT_ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
