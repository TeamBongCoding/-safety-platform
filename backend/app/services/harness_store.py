"""ESP32(또는 시뮬레이터)가 보내는 고리 상태를 메모리에 보관."""
import time

_devices: dict[str, dict] = {}
STALE_SEC = 5   # 이 시간 이상 수신 없으면 상태 불명 처리


def update(device_id: str, hook_closed: bool, rfid_tag: str | None):
    _devices[device_id] = {
        "hook_closed": hook_closed,
        "rfid_tag": rfid_tag,          # 승인된 체결 지점 태그 ID
        "ts": time.time(),
    }


def get(device_id: str = "worker-1") -> dict:
    d = _devices.get(device_id)
    if not d or time.time() - d["ts"] > STALE_SEC:
        return {"hook_closed": False, "rfid_tag": None, "online": False}
    return {**d, "online": True}