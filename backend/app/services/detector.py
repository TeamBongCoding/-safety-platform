"""YOLOv8 추론 래퍼 — Hard Hat Workers 파인튜닝 모델(best.pt) 사용."""
import threading
from ultralytics import YOLO

from ..config import HELMET_MODEL_PATH, MODEL_CONFIDENCE, PERSON_MODEL_PATH

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

    def _detect(self, frame):
        out = []
        r1 = self.helmet_model(frame, conf=MODEL_CONFIDENCE, verbose=False)[0]
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
            verbose=False,
        )[0]
        for b in r2.boxes:
            out.append({"cls": "person", "conf": float(b.conf),
                        "box": [float(v) for v in b.xyxy[0]]})
        return out


detector = Detector()
