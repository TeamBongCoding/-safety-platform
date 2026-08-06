import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth import require_current_site
from ..config import APPROVED_RFID_TAGS, DEVICE_API_KEY
from ..models import Site
from ..services import harness_store
from ..services.safety_rules import ZONE_TYPES  # noqa (참조용)

router = APIRouter(prefix="/api/harness", tags=["harness"])

class HarnessState(BaseModel):
    site_id: int
    device_id: str = "worker-1"
    hook_closed: bool
    rfid_tag: str | None = None


@router.post("/state")
def post_state(s: HarnessState, x_device_key: str | None = Header(default=None)):
    if not x_device_key or not hmac.compare_digest(x_device_key, DEVICE_API_KEY):
        raise HTTPException(status_code=401, detail="유효한 장치 키가 필요합니다.")
    # "고리 닫힘 + 승인된 지점 태그"일 때만 진짜 체결로 인정
    valid = s.hook_closed and (s.rfid_tag in APPROVED_RFID_TAGS)
    harness_store.update(s.site_id, s.device_id, valid, s.rfid_tag)

    # 서버가 진동 여부를 응답 → ESP32가 이 값 보고 모터 구동
    from ..state import latest_warn_devices   # 분석 루프가 채우는 set
    return {"ok": True, "vibrate": (s.site_id, s.device_id) in latest_warn_devices}


@router.get("/state")
def get_state(
    device_id: str = "worker-1",
    site: Site = Depends(require_current_site),
):
    return harness_store.get(site.id, device_id)
