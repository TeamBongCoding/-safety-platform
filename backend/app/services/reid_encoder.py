"""Lightweight FastReID inference using the existing Market1501 BoT R50 checkpoint."""

from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

from ..config import FASTREID_WEIGHTS_PATH, REID_DEVICE, REID_ENABLED

logger = logging.getLogger(__name__)


class FastReIDEncoder:
    """Load FastReID's R50 backbone without requiring the full FastReID package."""

    def __init__(self):
        self._lock = threading.Lock()
        self._load_attempted = False
        self._model = None
        self._device = None
        self._pixel_mean = None
        self._pixel_std = None

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._model is not None

    def _ensure_loaded(self) -> None:
        if self._load_attempted:
            return
        with self._lock:
            if self._load_attempted:
                return
            self._load_attempted = True
            if not REID_ENABLED:
                logger.info("FastReID disabled by REID_ENABLED=0")
                return
            if not FASTREID_WEIGHTS_PATH.is_file():
                logger.warning("FastReID weights not found: %s", FASTREID_WEIGHTS_PATH)
                return

            try:
                import torch
                from torchvision.models import resnet50

                requested = REID_DEVICE.strip().lower()
                if requested == "auto":
                    requested = "cuda" if torch.cuda.is_available() else "cpu"
                device = torch.device(requested)

                checkpoint = torch.load(
                    FASTREID_WEIGHTS_PATH,
                    map_location="cpu",
                    weights_only=False,
                )
                state = checkpoint.get("model", checkpoint)
                model = resnet50(weights=None)
                # FastReID's bag-of-tricks R50 uses stride 1 in the last stage.
                model.layer4[0].conv2.stride = (1, 1)
                model.layer4[0].downsample[0].stride = (1, 1)
                model.fc = torch.nn.Identity()

                backbone = {
                    key.removeprefix("backbone."): value
                    for key, value in state.items()
                    if key.startswith("backbone.")
                }
                incompatible = model.load_state_dict(backbone, strict=False)
                unexpected = [key for key in incompatible.unexpected_keys if not key.startswith("fc.")]
                if unexpected:
                    raise RuntimeError(f"unexpected FastReID weights: {unexpected[:5]}")

                model.eval().to(device)
                self._model = model
                self._device = device
                self._pixel_mean = state.get(
                    "pixel_mean",
                    torch.tensor([123.675, 116.28, 103.53]).reshape(1, 3, 1, 1),
                ).to(device)
                self._pixel_std = state.get(
                    "pixel_std",
                    torch.tensor([58.395, 57.12, 57.375]).reshape(1, 3, 1, 1),
                ).to(device)
                logger.info("FastReID loaded on %s from %s", device, FASTREID_WEIGHTS_PATH)
            except Exception:
                logger.exception("FastReID could not be loaded; color descriptor fallback will be used")
                self._model = None

    def extract(self, frame: np.ndarray, boxes: list[list[float]]) -> np.ndarray | None:
        """Return one normalized 2048-D embedding for every input box."""
        if not boxes:
            return np.empty((0, 2048), dtype=np.float32)
        self._ensure_loaded()
        if self._model is None:
            return None

        height, width = frame.shape[:2]
        crops = []
        for box in boxes:
            x1, y1, x2, y2 = [int(round(value)) for value in box]
            x1, x2 = sorted((max(0, x1), min(width, x2)))
            y1, y2 = sorted((max(0, y1), min(height, y2)))
            if x2 - x1 < 4 or y2 - y1 < 8:
                crop = np.zeros((256, 128, 3), dtype=np.uint8)
            else:
                crop = cv2.resize(
                    frame[y1:y2, x1:x2],
                    (128, 256),
                    interpolation=cv2.INTER_AREA,
                )
            crops.append(np.ascontiguousarray(crop[:, :, ::-1]))  # BGR -> RGB

        import torch

        batch = torch.from_numpy(np.stack(crops)).permute(0, 3, 1, 2).float().to(self._device)
        batch = (batch - self._pixel_mean) / self._pixel_std
        with self._lock, torch.inference_mode():
            features = self._model(batch)
            features = torch.nn.functional.normalize(features, dim=1)
        return features.cpu().numpy().astype(np.float32, copy=False)


reid_encoder = FastReIDEncoder()
