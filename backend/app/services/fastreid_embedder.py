"""Cross-platform inference for the official FastReID BoT ResNet-50 checkpoint."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as functional
from torchvision.models import resnet50

from ..config import FASTREID_WEIGHTS_PATH, REID_DEVICE


class FastReIDEmbedder:
    """Load FastReID backbone weights without requiring its optional Cython stack."""

    def __init__(self):
        self.device = self._resolve_device(REID_DEVICE)
        self.model = None
        self.error: str | None = None
        weights_path = Path(FASTREID_WEIGHTS_PATH)
        if not weights_path.is_file():
            self.error = f"FastReID weights not found: {weights_path}"
            return
        try:
            self.model = self._load_model(weights_path).to(self.device).eval()
        except Exception as exc:
            self.error = f"FastReID load failed: {exc}"

    @staticmethod
    def _resolve_device(requested: str) -> torch.device:
        if requested == "auto":
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if requested.startswith("cuda") and not torch.cuda.is_available():
            return torch.device("cpu")
        return torch.device(requested)

    @staticmethod
    def _load_model(weights_path: Path):
        model = resnet50(weights=None)
        # Official Base-bagtricks.yml uses LAST_STRIDE=1 and NECK_FEAT=before.
        model.layer4[0].conv2.stride = (1, 1)
        model.layer4[0].downsample[0].stride = (1, 1)
        model.fc = torch.nn.Identity()

        checkpoint = torch.load(
            weights_path,
            map_location="cpu",
            weights_only=False,
        )
        state_dict = checkpoint.get("model", checkpoint)
        backbone = {
            key.removeprefix("backbone."): value
            for key, value in state_dict.items()
            if key.startswith("backbone.")
        }
        incompatible = model.load_state_dict(backbone, strict=False)
        if incompatible.unexpected_keys or incompatible.missing_keys:
            raise RuntimeError(
                "incompatible checkpoint "
                f"(missing={incompatible.missing_keys}, "
                f"unexpected={incompatible.unexpected_keys})"
            )
        return model

    @property
    def available(self) -> bool:
        return self.model is not None

    def embed(self, crops: list[np.ndarray]) -> list[np.ndarray]:
        if self.model is None:
            raise RuntimeError(self.error or "FastReID is unavailable")
        tensors = []
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)[:, None, None]
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)[:, None, None]
        for crop in crops:
            resized = cv2.resize(crop, (128, 256), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            normalized = (rgb.transpose(2, 0, 1).astype(np.float32) / 255.0 - mean) / std
            tensors.append(torch.from_numpy(normalized))

        batch = torch.stack(tensors).to(self.device)
        with torch.inference_mode():
            features = functional.normalize(self.model(batch), p=2, dim=1)
        return [feature.detach().cpu().numpy().astype(np.float32) for feature in features]
