"""YOLOv8 추론 래퍼 — Hard Hat Workers 파인튜닝 모델(best.pt) 사용."""
import threading
from ultralytics import YOLO

from ..config import HELMET_MODEL_PATH, MODEL_CONFIDENCE, MODEL_IMAGE_SIZE, PERSON_MODEL_PATH

# 학습 데이터셋 클래스 → 우리 표준 이름
CLASS_MAP = {
    "head": "no-helmet",    # 안전모 안 쓴 머리 = 미착용
    "helmet": "helmet",
    "person": "person",     # 이 모델에선 성능 낮음 (4단계에서 COCO 모델로 보완)
}


class Detector:
    def __init__(self):
        self._lock = threading.Lock()
        self.helmet_model = YOLO(HELMET_MODEL_PATH)   # head/helmet
        self.person_model = YOLO(PERSON_MODEL_PATH)   # person (COCO)

    def detect(self, frame):
        with self._lock:
            return self._detect(frame)

    def detect_batch(self, frames):
        """Run both YOLO models over a frame batch for offline evaluation."""
        if not frames:
            return []
        with self._lock:
            helmet_results = self.helmet_model(
                frames,
                conf=MODEL_CONFIDENCE,
                batch=len(frames),
                imgsz=MODEL_IMAGE_SIZE,
                verbose=False,
            )
            person_results = self.person_model(
                frames,
                conf=MODEL_CONFIDENCE,
                classes=[0],
                batch=len(frames),
                imgsz=MODEL_IMAGE_SIZE,
                verbose=False,
            )

        batches = []
        for helmet_result, person_result in zip(helmet_results, person_results):
            out = []
            for box in helmet_result.boxes:
                raw = helmet_result.names[int(box.cls)]
                if raw in ("head", "helmet"):
                    out.append({
                        "cls": {"head": "no-helmet"}.get(raw, raw),
                        "conf": float(box.conf),
                        "box": [float(value) for value in box.xyxy[0]],
                    })
            for box in person_result.boxes:
                out.append({
                    "cls": "person",
                    "conf": float(box.conf),
                    "box": [float(value) for value in box.xyxy[0]],
                })
            batches.append(out)
        return batches

    def _detect(self, frame):
        out = []
        r1 = self.helmet_model(
            frame,
            conf=MODEL_CONFIDENCE,
            imgsz=MODEL_IMAGE_SIZE,
            verbose=False,
        )[0]
        for b in r1.boxes:
            raw = r1.names[int(b.cls)]
            if raw in ("head", "helmet"):
                out.append({"cls": {"head": "no-helmet"}.get(raw, raw),
                            "conf": float(b.conf),
                            "box": [float(v) for v in b.xyxy[0]]})
        r2 = self.person_model(
            frame,
            conf=MODEL_CONFIDENCE,
            classes=[0],
            imgsz=MODEL_IMAGE_SIZE,
            verbose=False,
        )[0]
        for b in r2.boxes:
            detection = {"cls": "person", "conf": float(b.conf),
                         "box": [float(v) for v in b.xyxy[0]]}
            out.append(detection)
        return out


detector = Detector()
