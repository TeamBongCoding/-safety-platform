"""테스트 영상 → 박스 그린 mp4 저장. 2단계 완료 기준 검증용."""
import sys
import cv2

sys.path.insert(0, ".")
from app.services.detector import detector
from app.services.helmet_logic import find_violations, ViolationTracker

COLORS = {"person": (0, 200, 0), "helmet": (255, 180, 0), "no-helmet": (0, 0, 255)}
INFER_EVERY = 3   # 3프레임에 1번만 추론


def main(video_path: str, out_path: str = "output.mp4"):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"영상을 열 수 없습니다: {video_path}")
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    tracker = ViolationTracker()
    detections, frame_idx = [], 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % INFER_EVERY == 0:
            detections = detector.detect(frame)
            violations = find_violations(detections)
            if tracker.update(len(violations)):
                print(f"[이벤트] frame {frame_idx}: 안전모 미착용 {len(violations)}명")

        for d in detections:
            x1, y1, x2, y2 = map(int, d["box"])
            color = COLORS.get(d["cls"], (200, 200, 200))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f'{d["cls"]} {d["conf"]:.2f}', (x1, y1 - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        writer.write(frame)
        frame_idx += 1
        if frame_idx % 100 == 0:
            print(f"진행: {frame_idx} 프레임")

    cap.release()
    writer.release()
    print(f"저장 완료: {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "../data/videos/site1.mp4")