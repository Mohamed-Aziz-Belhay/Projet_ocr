#/app/classifiers/swin_classifier.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

try:
    import timm
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Le package 'timm' est requis pour charger le classifieur Swin. "
        "Installez-le avec: pip install timm"
    ) from e

from app.core.logging import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)


class ConvertToRGB:
    def __call__(self, img):
        return img.convert("RGB")


@dataclass
class SwinPrediction:
    predicted_class: Optional[str]
    confidence: float
    accepted: bool
    all_classes: List[Dict[str, Any]]
    model_name: Optional[str]
    img_size: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predicted_class": self.predicted_class,
            "confidence": self.confidence,
            "accepted": self.accepted,
            "all_classes": self.all_classes,
            "model_name": self.model_name,
            "img_size": self.img_size,
        }


class SwinDocumentClassifier:
    """
    Classifieur documentaire Swin aligné avec :
    - train_swin.py
    - evaluate_swin.py

    Attendu dans SWIN_MODEL_PATH :
    - soit le dossier contenant :
        best_model.pt
        label_config.json
        training_config.json
    - soit directement le chemin vers best_model.pt
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.device = self._resolve_device()

        self.model_dir: Optional[Path] = None
        self.checkpoint_path: Optional[Path] = None
        self.label_config_path: Optional[Path] = None
        self.training_config_path: Optional[Path] = None

        self.model = None
        self.model_name: Optional[str] = None
        self.img_size: int = 224
        self.threshold: float = float(getattr(self.settings, "SWIN_CONFIDENCE_THRESHOLD", 0.75) or 0.75)

        self.class_to_idx: Dict[str, int] = {}
        self.idx_to_class: Dict[int, str] = {}
        self.num_classes: int = 0

        self.transform = None
        self._loaded = False
        self._load_error: Optional[str] = None

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def is_configured(self) -> bool:
        self._ensure_loaded()
        return self._loaded

    def get_status(self) -> Dict[str, Any]:
        self._ensure_loaded()
        return {
            "configured": self._loaded,
            "device": self.device,
            "model_dir": str(self.model_dir) if self.model_dir else None,
            "checkpoint_path": str(self.checkpoint_path) if self.checkpoint_path else None,
            "label_config_path": str(self.label_config_path) if self.label_config_path else None,
            "training_config_path": str(self.training_config_path) if self.training_config_path else None,
            "model_name": self.model_name,
            "img_size": self.img_size,
            "num_classes": self.num_classes,
            "classes": [self.idx_to_class[i] for i in sorted(self.idx_to_class)],
            "threshold": self.threshold,
            "error": self._load_error,
        }

    def reload(self) -> Dict[str, Any]:
        self._loaded = False
        self._load_error = None
        self.model = None
        self.transform = None
        self.class_to_idx = {}
        self.idx_to_class = {}
        self.num_classes = 0
        self.model_name = None
        self.img_size = 224
        self.model_dir = None
        self.checkpoint_path = None
        self.label_config_path = None
        self.training_config_path = None
        self._ensure_loaded()
        return self.get_status()

    def classify(self, image_bgr: np.ndarray) -> Optional[str]:
        details = self.classify_with_details(image_bgr)
        if "error" in details:
            return None
        return details.get("predicted_class")

    def classify_with_details(self, image_bgr: np.ndarray) -> Dict[str, Any]:
        self._ensure_loaded()
        if not self._loaded:
            return {"error": self._load_error or "Swin model not configured"}

        if image_bgr is None or not isinstance(image_bgr, np.ndarray):
            return {"error": "Invalid image input"}

        try:
            pil_img = self._cv2_to_pil(image_bgr)
            x = self.transform(pil_img).unsqueeze(0).to(self.device)

            with torch.no_grad():
                logits = self.model(x)
                probs = torch.softmax(logits, dim=1).cpu().numpy()[0]

            top_indices = np.argsort(-probs).tolist()
            all_classes = [
                {
                    "class_name": self.idx_to_class[int(i)],
                    "confidence": float(probs[int(i)]),
                }
                for i in top_indices
            ]

            best_idx = int(top_indices[0])
            predicted_class = self.idx_to_class[best_idx]
            confidence = float(probs[best_idx])
            accepted = confidence >= self.threshold

            pred = SwinPrediction(
                predicted_class=predicted_class,
                confidence=confidence,
                accepted=accepted,
                all_classes=all_classes,
                model_name=self.model_name,
                img_size=self.img_size,
            )
            return pred.to_dict()

        except Exception as exc:
            log.error("Swin classification failed", extra={"error": str(exc)})
            return {"error": str(exc)}

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _resolve_device(self) -> str:
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load()

    def _load(self) -> None:
        try:
            model_path_value = getattr(self.settings, "SWIN_MODEL_PATH", None)
            if not model_path_value:
                self._load_error = "SWIN_MODEL_PATH not configured"
                return

            raw_path = Path(str(model_path_value)).expanduser()

            if raw_path.is_file():
                # Cas où SWIN_MODEL_PATH pointe directement vers best_model.pt
                self.checkpoint_path = raw_path
                self.model_dir = raw_path.parent
            else:
                # Cas où SWIN_MODEL_PATH pointe vers le dossier modèle
                self.model_dir = raw_path
                self.checkpoint_path = self.model_dir / "best_model.pt"

            self.label_config_path = self.model_dir / "label_config.json"
            self.training_config_path = self.model_dir / "training_config.json"

            if not self.checkpoint_path.exists():
                self._load_error = f"Checkpoint introuvable: {self.checkpoint_path}"
                return

            if not self.label_config_path.exists():
                self._load_error = f"label_config.json introuvable: {self.label_config_path}"
                return

            label_cfg = self._load_json(self.label_config_path)
            training_cfg = self._load_json(self.training_config_path) if self.training_config_path.exists() else {}

            self.class_to_idx = {
                str(k): int(v) for k, v in label_cfg["class_to_idx"].items()
            }
            self.idx_to_class = {int(v): str(k) for k, v in self.class_to_idx.items()}
            self.num_classes = int(label_cfg["num_classes"])

            self.model_name = str(training_cfg.get("model_name", "swin_tiny_patch4_window7_224"))
            self.img_size = int(training_cfg.get("img_size", 224))

            self.transform = transforms.Compose(
                [
                    ConvertToRGB(),
                    transforms.Resize((self.img_size, self.img_size)),
                    transforms.ToTensor(),
                    transforms.Normalize(
                        mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225],
                    ),
                ]
            )

            self.model = timm.create_model(
                model_name=self.model_name,
                pretrained=False,
                num_classes=self.num_classes,
            )
            self.model = self.model.to(self.device)

            ckpt = torch.load(
                self.checkpoint_path,
                map_location=self.device,
                weights_only=False,
            )
            self.model.load_state_dict(ckpt["model_state_dict"])
            self.model.eval()

            self._loaded = True
            self._load_error = None

            log.info(
                "Swin classifier loaded",
                extra={
                    "model_dir": str(self.model_dir),
                    "model_name": self.model_name,
                    "num_classes": self.num_classes,
                    "device": self.device,
                    "threshold": self.threshold,
                },
            )

        except Exception as exc:
            self._loaded = False
            self._load_error = str(exc)
            log.error("Failed to load Swin classifier", extra={"error": str(exc)})

    def _load_json(self, path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _cv2_to_pil(self, image_bgr: np.ndarray) -> Image.Image:
        if image_bgr.ndim == 2:
            return Image.fromarray(image_bgr)
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(image_rgb)


_classifier: Optional[SwinDocumentClassifier] = None


def get_swin_classifier() -> SwinDocumentClassifier:
    global _classifier
    if _classifier is None:
        _classifier = SwinDocumentClassifier()
    return _classifier