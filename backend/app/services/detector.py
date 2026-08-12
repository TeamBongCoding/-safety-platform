"""YOLOv8 추론 래퍼 — Hard Hat Workers 파인튜닝 모델(best.pt) 사용."""
import threading
from ultralytics import YOLO

from ..config import (
    HELMET_MODEL_PATH,
    MODEL_CONFIDENCE,
    PERSON_MODEL_PATH,
    REID_BACKEND,
    REID_IMAGE_SIZE,
)
from .fastreid_embedder import FastReIDEmbedder

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
        self.fastreid = FastReIDEmbedder() if REID_BACKEND == "fastreid" else None
        self.reid_backend = "fastreid" if self.fastreid and self.fastreid.available else "yolo"
        # Keep a separate predictor so embedding calls do not alter detection options.
        self.reid_model = YOLO(PERSON_MODEL_PATH) if self.reid_backend == "yolo" else None

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
        person_detections = []
        for b in r2.boxes:
            detection = {"cls": "person", "conf": float(b.conf),
                         "box": [float(v) for v in b.xyxy[0]]}
            person_detections.append(detection)
            out.append(detection)

        crops = []
        crop_indexes = []
        height, width = frame.shape[:2]
        for index, detection in enumerate(person_detections):
            x1, y1, x2, y2 = detection["box"]
            pad_x = (x2 - x1) * 0.06
            pad_y = (y2 - y1) * 0.03
            left = max(0, int(x1 - pad_x))
            top = max(0, int(y1 - pad_y))
            right = min(width, int(x2 + pad_x))
            bottom = min(height, int(y2 + pad_y))
            if right - left >= 4 and bottom - top >= 8:
                crops.append(frame[top:bottom, left:right])
                crop_indexes.append(index)

        if crops:
            try:
                if self.reid_backend == "fastreid":
                    embeddings = self.fastreid.embed(crops)
                else:
                    embeddings = self.reid_model.embed(
                        crops,
                        imgsz=REID_IMAGE_SIZE,
                        verbose=False,
                    )
                for index, embedding in zip(crop_indexes, embeddings):
                    if hasattr(embedding, "detach"):
                        embedding = embedding.detach().cpu().numpy()
                    person_detections[index]["reid_embedding"] = embedding.astype("float32")
                    person_detections[index]["reid_backend"] = self.reid_backend
            except Exception:
                # Person detection must remain available even if embedding fails.
                pass
        return out


detector = Detector()
