#app/models/swin/predictor.py
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import cv2
import timm
import torch
from PIL import Image
from torchvision import transforms

from app.core.logging import get_logger

log = get_logger(__name__)


class SwinDocumentClassifier:
    def __init__(
        self,
        checkpoint_path: str = "models/swin_doc_classifier/best.pt",
    ):
        self.checkpoint_path = Path(checkpoint_path)
        self.available = False
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.labels = []
        self.image_size = 224
        self.tf = None

        self._load()

    def _load(self) -> None:
        if not self.checkpoint_path.exists():
            log.warning(
                "Swin checkpoint not found",
                extra={"path": str(self.checkpoint_path)},
            )
            return

        ckpt = torch.load(
            self.checkpoint_path,
            map_location=self.device,
        )

        self.labels = ckpt["labels"]
        self.image_size = int(ckpt.get("image_size", 224))
        model_name = ckpt.get("model_name", "swin_tiny_patch4_window7_224")

        self.model = timm.create_model(
            model_name,
            pretrained=False,
            num_classes=len(self.labels),
        )

        self.model.load_state_dict(ckpt["state_dict"])
        self.model.to(self.device)
        self.model.eval()

        self.tf = transforms.Compose(
            [
                transforms.Resize((self.image_size, self.image_size)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=(0.485, 0.456, 0.406),
                    std=(0.229, 0.224, 0.225),
                ),
            ]
        )

        self.available = True

    def _template_id_for_class(self, class_name: str) -> str:
        return f"midv_{class_name}"

    def _document_type_for_class(self, class_name: str) -> str:
        low = str(class_name or "").lower()

        if "passport" in low:
            return "passport"

        return "id_document"

    def _predict_pil(
        self,
        img: Image.Image,
    ) -> Dict[str, Any]:
        if not self.available or self.model is None or self.tf is None:
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "swin_unavailable",
            }

        img = img.convert("RGB")
        x = self.tf(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            logits = self.model(x)
            probs = torch.softmax(logits, dim=1)[0]
            conf, idx = torch.max(probs, dim=0)

        class_name = self.labels[int(idx.item())]
        confidence = float(conf.item())

        return {
            "available": True,
            "document_class": class_name,
            "document_type": self._document_type_for_class(class_name),
            "template_id": self._template_id_for_class(class_name),
            "confidence": round(confidence, 4),
            "method": "swin_image_classifier",
        }

    def predict_image_path(
        self,
        image_path: str,
    ) -> Dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "swin_unavailable",
            }

        path = Path(image_path)

        if not path.exists():
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "image_path_not_found",
                "error": str(path),
            }

        with Image.open(path) as img:
            return self._predict_pil(img)

    def predict_array(
        self,
        image_bgr,
    ) -> Dict[str, Any]:
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "swin_empty_image",
            }

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)

        return self._predict_pil(pil_img)


@lru_cache(maxsize=1)
def get_swin_document_classifier() -> SwinDocumentClassifier:
    return SwinDocumentClassifier()

'''from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import cv2
import timm
import torch
from PIL import Image
from torchvision import transforms

from app.core.logging import get_logger

log = get_logger(__name__)


class SwinDocumentClassifier:
    def __init__(
        self,
        checkpoint_path: str = "models/swin_doc_classifier/best.pt",
    ):
        self.checkpoint_path = Path(checkpoint_path)
        self.available = False
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.labels = []
        self.image_size = 224
        self.tf = None

        self._load()

    def _load(self) -> None:
        if not self.checkpoint_path.exists():
            log.warning(
                "Swin checkpoint not found",
                extra={"path": str(self.checkpoint_path)},
            )
            return

        ckpt = torch.load(
            self.checkpoint_path,
            map_location=self.device,
        )

        self.labels = ckpt["labels"]
        self.image_size = int(ckpt.get("image_size", 224))
        model_name = ckpt.get("model_name", "swin_tiny_patch4_window7_224")

        self.model = timm.create_model(
            model_name,
            pretrained=False,
            num_classes=len(self.labels),
        )

        self.model.load_state_dict(ckpt["state_dict"])
        self.model.to(self.device)
        self.model.eval()

        self.tf = transforms.Compose(
            [
                transforms.Resize((self.image_size, self.image_size)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=(0.485, 0.456, 0.406),
                    std=(0.229, 0.224, 0.225),
                ),
            ]
        )

        self.available = True

    def _template_id_for_class(self, class_name: str) -> str:
        return f"midv_{class_name}"

    def _document_type_for_class(self, class_name: str) -> str:
        if "passport" in class_name:
            return "passport"

        return "id_document"

    def _predict_pil(
        self,
        img: Image.Image,
    ) -> Dict[str, Any]:
        if not self.available or self.model is None or self.tf is None:
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "swin_unavailable",
            }

        img = img.convert("RGB")
        x = self.tf(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            logits = self.model(x)
            probs = torch.softmax(logits, dim=1)[0]
            conf, idx = torch.max(probs, dim=0)

        class_name = self.labels[int(idx.item())]
        confidence = float(conf.item())

        return {
            "available": True,
            "document_class": class_name,
            "document_type": self._document_type_for_class(class_name),
            "template_id": self._template_id_for_class(class_name),
            "confidence": round(confidence, 4),
            "method": "swin_image_classifier",
        }

    def predict_image_path(
        self,
        image_path: str,
    ) -> Dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "swin_unavailable",
            }

        with Image.open(image_path) as img:
            return self._predict_pil(img)

    def predict_array(
        self,
        image_bgr,
    ) -> Dict[str, Any]:
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return {
                "available": False,
                "document_class": None,
                "document_type": None,
                "template_id": None,
                "confidence": 0.0,
                "method": "swin_empty_image",
            }

        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)

        return self._predict_pil(pil_img)


@lru_cache(maxsize=1)
def get_swin_document_classifier() -> SwinDocumentClassifier:
    return SwinDocumentClassifier()'''