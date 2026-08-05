import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import ANALYSIS_ENABLED, CORS_ORIGINS
from .database import Base, engine
from .routers import analysis, events, harness, zones
from .services.analysis_service import analysis_service

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if ANALYSIS_ENABLED:
        analysis_service.start()
    yield
    analysis_service.stop()


app = FastAPI(title="AI 안전관리 플랫폼", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)
app.include_router(zones.router)
app.include_router(harness.router)
app.include_router(analysis.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            summary = await asyncio.to_thread(analysis_service.get_summary)
            await ws.send_json(summary)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
