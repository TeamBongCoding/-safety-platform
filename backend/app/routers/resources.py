from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..database import get_db
from ..models import Camera, Site, Worker
from ..schemas import CameraCreate, CameraOut, WorkerCreate, WorkerOut
from ..services.analysis_service import analysis_registry

router = APIRouter(prefix="/api", tags=["resources"])


@router.get("/workers", response_model=list[WorkerOut])
def list_workers(site: Site = Depends(require_current_site), db: Session = Depends(get_db)):
    return db.scalars(select(Worker).where(Worker.site_id == site.id).order_by(Worker.id)).all()


@router.post("/workers", response_model=WorkerOut, status_code=status.HTTP_201_CREATED)
def create_worker(
    payload: WorkerCreate,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    worker = Worker(site_id=site.id, **payload.model_dump())
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return worker


@router.get("/cameras", response_model=list[CameraOut])
def list_cameras(site: Site = Depends(require_current_site), db: Session = Depends(get_db)):
    return db.scalars(select(Camera).where(Camera.site_id == site.id).order_by(Camera.id)).all()


@router.post("/cameras", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(
    payload: CameraCreate,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    camera = Camera(site_id=site.id, **payload.model_dump())
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return camera


@router.put("/cameras/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: int,
    payload: CameraCreate,
    site: Site = Depends(require_current_site),
    db: Session = Depends(get_db),
):
    camera = db.scalar(
        select(Camera).where(Camera.id == camera_id, Camera.site_id == site.id)
    )
    if not camera:
        raise HTTPException(status_code=404, detail="카메라를 찾을 수 없습니다.")
    camera.name = payload.name
    camera.source = payload.source
    db.commit()
    db.refresh(camera)
    # 기존 분석 서비스를 중단시켜 다음 스트림 요청 시 새 source로 재시작되게 한다
    analysis_registry.stop_camera(site.id, camera.id)
    return camera
