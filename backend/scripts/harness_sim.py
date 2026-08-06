"""ESP32 시뮬레이터 — 키보드로 고리 상태 토글하며 서버에 전송.
o: 고리 열림 / c: 닫힘(승인 태그) / x: 닫힘(미승인 태그) / q: 종료
"""
import requests, threading, time

from app.config import BACKEND_BASE_URL, DEVICE_API_KEY, HARNESS_SIM_RFID_TAG, HARNESS_SITE_ID


URL = f"{BACKEND_BASE_URL}/api/harness/state"
state = {"site_id": HARNESS_SITE_ID, "hook_closed": False, "rfid_tag": None}


def sender():
    while True:
        try:
            r = requests.post(
                URL,
                json={"device_id": "worker-1", **state},
                headers={"X-Device-Key": DEVICE_API_KEY},
                timeout=2,
            )
            if r.json().get("vibrate"):
                print("*** 진동모터 작동! (경고 수신) ***")
        except Exception as e:
            print("전송 실패:", e)
        time.sleep(1)


threading.Thread(target=sender, daemon=True).start()
print("o=열림 c=체결(승인) x=체결(미승인) q=종료")
while True:
    k = input("> ").strip()
    if k == "o":
        state.update(hook_closed=False, rfid_tag=None)
    elif k == "c":
        state.update(hook_closed=True, rfid_tag=HARNESS_SIM_RFID_TAG)
    elif k == "x":
        state.update(hook_closed=True, rfid_tag="UNKNOWN")
    elif k == "q":
        break
    print("현재 상태:", state)
