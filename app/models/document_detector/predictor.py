#document_detector/predictor.py
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class DetectionResult:
    found: bool
    bbox_xyxy: Optional[list[float]]
    confidence: float
    class_name: Optional[str]
    method: str
    error: Optional[str] = None

    def model_dump(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "bbox_xyxy": self.bbox_xyxy,
            "confidence": self.confidence,
            "class_name": self.class_name,
            "method": self.method,
            "error": self.error,
        }


class DocumentDetector:
    """
    YOLO-based document detector.

    Role:
    - find the full document/card/passport inside a larger image
    - return one bbox
    - do not classify the document type
    - do not extract fields
    """

    def __init__(
        self,
        checkpoint_path: str = "models/document_detector/best.pt",
        conf_threshold: float = 0.25,
        imgsz: int = 1024,
    ):
        self.checkpoint_path = Path(checkpoint_path)
        self.conf_threshold = conf_threshold
        self.imgsz = imgsz
        self.available = False
        self.model = None

        self._load()

    def _load(self) -> None:
        if not self.checkpoint_path.exists():
            log.warning(
                "Document detector checkpoint not found",
                extra={"path": str(self.checkpoint_path)},
            )
            return

        try:
            from ultralytics import YOLO

            self.model = YOLO(str(self.checkpoint_path))
            self.available = True

        except Exception as exc:
            log.warning(
                "Document detector load failed",
                extra={"path": str(self.checkpoint_path), "error": str(exc)},
            )
            self.available = False
            self.model = None

    def detect(
        self,
        image: np.ndarray,
    ) -> DetectionResult:
        if image is None or image.size == 0:
            return DetectionResult(
                found=False,
                bbox_xyxy=None,
                confidence=0.0,
                class_name=None,
                method="yolo_document_detector",
                error="empty_image",
            )

        if not self.available or self.model is None:
            return DetectionResult(
                found=False,
                bbox_xyxy=None,
                confidence=0.0,
                class_name=None,
                method="yolo_unavailable",
                error="checkpoint_missing_or_load_failed",
            )

        try:
            results = self.model.predict(
                source=image,
                imgsz=self.imgsz,
                conf=self.conf_threshold,
                verbose=False,
            )

            if not results:
                return DetectionResult(
                    found=False,
                    bbox_xyxy=None,
                    confidence=0.0,
                    class_name=None,
                    method="yolo_document_detector",
                    error="no_results",
                )

            result = results[0]
            boxes = getattr(result, "boxes", None)

            if boxes is None or len(boxes) == 0:
                return DetectionResult(
                    found=False,
                    bbox_xyxy=None,
                    confidence=0.0,
                    class_name=None,
                    method="yolo_document_detector",
                    error="no_box",
                )

            best = None
            best_score = -1.0

            h, w = image.shape[:2]
            image_area = max(float(w * h), 1.0)

            for box in boxes:
                xyxy = box.xyxy[0].detach().cpu().numpy().astype(float).tolist()
                conf = float(box.conf[0].detach().cpu().item())

                x1, y1, x2, y2 = xyxy
                area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                area_ratio = area / image_area

                # Prefer a confident and reasonably large document box.
                score = conf + min(area_ratio, 0.35)

                if score > best_score:
                    best_score = score
                    best = {
                        "xyxy": xyxy,
                        "conf": conf,
                    }

            if best is None:
                return DetectionResult(
                    found=False,
                    bbox_xyxy=None,
                    confidence=0.0,
                    class_name=None,
                    method="yolo_document_detector",
                    error="no_valid_box",
                )

            return DetectionResult(
                found=True,
                bbox_xyxy=best["xyxy"],
                confidence=round(float(best["conf"]), 4),
                class_name="document",
                method="yolo_document_detector",
                error=None,
            )

        except Exception as exc:
            return DetectionResult(
                found=False,
                bbox_xyxy=None,
                confidence=0.0,
                class_name=None,
                method="yolo_document_detector",
                error=str(exc),
            )


@lru_cache(maxsize=1)
def get_document_detector() -> DocumentDetector:
    return DocumentDetector()