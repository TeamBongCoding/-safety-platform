"""통합 파이프라인 검증: 감지 + 구역 + 고리 상태 + 오버레이.
서버가 켜져 있어야 함 (구역은 DB에서, 고리 상태는 서버 API에서 주기 갱신).
실행: python -m scripts.analyze_video2 ../data/videos/site1.mp4
"""
import json
import sys

import cv2
import requests
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from app.config import BACKEND_BASE_URL
from app.database import SessionLocal
from app.models import Zone
from app.services import harness_store
from app.services.detector import detector
from app.services.pipeline import process_frame

INFER_EVERY = 3      # 3프레임에 1번 추론
HARNESS_EVERY = 30   # 약 1초마다 고리 상태 갱신


def load_zones():
    db = SessionLocal()
    zones = []
    for z in db.query(Zone).all():
        poly = json.loads(z.polygon)
        zones.append({"id": z.id, "zone_type": z.zone_type,
                      "polygon": poly, "poly": Polygon(poly)})
    db.close()
    print(f"구역 {len(zones)}개 로드")
    return zones


def refresh_harness():
    """서버에서 최신 고리 상태를 받아 로컬 harness_store에 주입."""
    try:
        s = requests.get(f"{BACKEND_BASE_URL}/api/harness/state",
                         timeout=1).json()
        harness_store.update("worker-1",
                             s.get("hook_closed", False),
                             s.get("rfid_tag"))
    except Exception:
        pass  # 서버 미접속 시 기존 상태 유지 (곧 stale 처리 → 미체결 간주)


def main(video_path, out_path="output2.mp4"):
    zones = load_zones()

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("영상을 열 수 없습니다:", video_path)
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (w, h))

    detections, idx = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        if idx % HARNESS_EVERY == 0:
            refresh_harness()
        if idx % INFER_EVERY == 0:
            detections = detector.detect(frame)

        frame, events = process_frame(frame, detections, zones, w, h)
        for e in events:
            print(f"[ALERT] frame {idx}: {e['type']}")

        writer.write(frame)
        idx += 1
        if idx % 100 == 0:
            print("진행:", idx)

    cap.release()
    writer.release()
    print("저장 완료:", out_path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../data/videos/site1.mp4")
