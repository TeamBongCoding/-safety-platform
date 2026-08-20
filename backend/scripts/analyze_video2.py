"""Offline test-video analysis with local tracking and safety zones.

Run from backend: python -m scripts.analyze_video2 <video_path>
"""

import json
import sys

import cv2
from shapely.geometry import Polygon

sys.path.insert(0, ".")
from app.database import SessionLocal
from app.models import Zone
from app.services.detector import detector
from app.services.person_tracking import CameraPersonTracker
from app.services.pipeline import process_frame

INFER_EVERY = 3


def load_zones():
    with SessionLocal() as db:
        zones = []
        for zone in db.query(Zone).all():
            polygon = json.loads(zone.polygon)
            zones.append({
                "id": zone.id,
                "zone_type": zone.zone_type,
                "polygon": polygon,
                "poly": Polygon(polygon),
            })
    print(f"구역 {len(zones)}개 로드")
    return zones


def main(video_path, out_path="output2.mp4"):
    zones = load_zones()
    tracker = CameraPersonTracker()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("영상을 열 수 없습니다:", video_path)
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(
        out_path,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )

    detections, frame_index = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % INFER_EVERY == 0:
            detections = detector.detect(frame)
        frame, events, _ = process_frame(
            frame,
            detections,
            zones,
            width,
            height,
            tracker,
            include_status=True,
        )
        for event in events:
            print(f"[ALERT] frame {frame_index}: {event['type']}")
        writer.write(frame)
        frame_index += 1

    cap.release()
    writer.release()
    print("저장 완료:", out_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python -m scripts.analyze_video2 <video_path>")

    main(sys.argv[1])
