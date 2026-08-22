"""Ephemeral browser-serial BLE observations for the current demo site."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..auth import require_current_site
from ..config import KMA_API_KEY
from ..models import Site
from ..services.analysis_service import analysis_registry
from ..services.heat_service import heat_registry

router = APIRouter(prefix="/api/ble", tags=["ble"])


def _service(site: Site):
    heat_service = heat_registry.get(site.id, site.latitude, site.longitude, KMA_API_KEY)
    return analysis_registry.get(
        site.id,
        is_outdoor=site.is_outdoor,
        heat_service=heat_service,
    )


class BleObservation(BaseModel):
    receiver_id: str = Field(default="receiver-1", pattern=r"^receiver-[12]$")
    uuid: str = Field(min_length=32, max_length=36)
    major: int = Field(ge=0, le=65535)
    minor: int = Field(ge=0, le=65535)
    rssi: int = Field(ge=-127, le=20)
    measured_power: int | None = Field(default=None, ge=-128, le=127)

    @field_validator("uuid")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        normalized = value.replace("-", "").upper()
        if not re.fullmatch(r"[0-9A-F]{32}", normalized):
            raise ValueError("UUID는 32자리 16진수여야 합니다.")
        return normalized


class BleBinding(BaseModel):
    tag_key: str = Field(min_length=36, max_length=80)
    track_id: str = Field(pattern=r"^person-\d{6,}$")


class BleUnbinding(BaseModel):
    tag_key: str = Field(min_length=36, max_length=80)


class IdentityMerge(BaseModel):
    primary_track_id: str = Field(pattern=r"^person-\d{6,}$")
    duplicate_track_id: str = Field(pattern=r"^person-\d{6,}$")


@router.get("")
def list_tags(site: Site = Depends(require_current_site)):
    return _service(site).get_ble_tags()


@router.post("/observations")
def observe(payload: BleObservation, site: Site = Depends(require_current_site)):
    return _service(site).submit_ble_observation(**payload.model_dump())


@router.post("/bindings")
def bind(payload: BleBinding, site: Site = Depends(require_current_site)):
    try:
        return _service(site).bind_ble_tag(payload.tag_key, payload.track_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/bindings/remove")
def unbind(payload: BleUnbinding, site: Site = Depends(require_current_site)):
    _service(site).unbind_ble_tag(payload.tag_key)
    return {"ok": True}


@router.post("/identities/merge")
def merge_identity(
    payload: IdentityMerge,
    site: Site = Depends(require_current_site),
):
    try:
        return _service(site).merge_person_ids(
            payload.primary_track_id,
            payload.duplicate_track_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
