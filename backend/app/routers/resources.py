from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_current_site
from ..database import get_db
from ..models import Camera, Site, Worker
from ..schemas import CameraCreate, CameraOut, WorkerCreate, WorkerOut

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
