"""
app/models/layout/layout_detector.py
Phase-D: Layout analysis — detect text regions, tables, and field zones.

Two modes:
  1. Rule-based (default): OpenCV contour detection — no model needed
  2. Model-based: DIT LayoutLM (when LAYOUT_MODEL_PATH is set)

The goal is to localise field regions BEFORE OCR,
improving extraction of structured documents (tables, forms).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from app.core.settings import get_settings
from app.core.logging import get_logger

log      = settings = get_settings()
log      = get_logger(__name__)
settings = get_settings()


@dataclass
class LayoutRegion:
    """A detected region in a document image."""
    region_type: str            # "text_block" | "table" | "header" | "footer" | "field"
    bbox: Tuple[int, int, int, int]  # x, y, w, h (pixels)
    confidence: float = 1.0
    label: Optional[str] = None      # e.g. field name if matched


@dataclass
class LayoutResult:
    regions: List[LayoutRegion] = field(default_factory=list)
    page_width: int = 0
    page_height: int = 0
    orientation: str = "portrait"    # portrait | landscape
    has_table: bool = False
    column_count: int = 1


class RuleBasedLayoutDetector:
    """
    OpenCV-based layout detector.
    Detects text blocks and tables via morphological operations.
    No model required — runs on any machine.
    """
    def detect(self, image: np.ndarray) -> LayoutResult:
        import cv2
        h, w = image.shape[:2]
        gray   = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Detect text blocks via dilation
        kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 10))
        dilated  = cv2.dilate(binary, kernel_h)
        dilated  = cv2.dilate(dilated, kernel_v)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        regions: List[LayoutRegion] = []
        for cnt in contours:
            x, y, rw, rh = cv2.boundingRect(cnt)
            area_ratio = (rw * rh) / (w * h)
            if area_ratio < 0.001:
                continue  # skip tiny noise
            # Classify by aspect + position
            if rh < h * 0.05 and y < h * 0.15:
                rtype = "header"
            elif rh < h * 0.05 and y > h * 0.85:
                rtype = "footer"
            elif rw > w * 0.7 and rh > h * 0.1:
                rtype = "table"
            else:
                rtype = "text_block"
            regions.append(LayoutRegion(region_type=rtype, bbox=(x, y, rw, rh)))

        # Table detection heuristic
        h_lines = _detect_lines(binary, "horizontal")
        v_lines = _detect_lines(binary, "vertical")
        has_table = len(h_lines) >= 3 and len(v_lines) >= 2

        # Column estimation
        if regions:
            x_centers = [(r.bbox[0] + r.bbox[2]) / 2 for r in regions if r.region_type == "text_block"]
            column_count = _estimate_columns(x_centers)
        else:
            column_count = 1

        return LayoutResult(
            regions=regions,
            page_width=w,
            page_height=h,
            orientation="landscape" if w > h else "portrait",
            has_table=has_table,
            column_count=column_count,
        )


def _detect_lines(binary: np.ndarray, direction: str) -> list:
    import cv2
    if direction == "horizontal":
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (binary.shape[1] // 10, 1))
    else:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, binary.shape[0] // 10))
    lines_img = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(lines_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours


def _estimate_columns(x_centers: list) -> int:
    """Cluster x-centers to estimate column count (naive k-means heuristic)."""
    if not x_centers:
        return 1
    xs = sorted(x_centers)
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    large_gaps = sum(1 for g in gaps if g > 0.25)
    return min(large_gaps + 1, 3)


class ModelBasedLayoutDetector:
    """
    HuggingFace DIT LayoutLM-based detector.
    Requires LAYOUT_MODEL_PATH to be set.
    Falls back to RuleBasedLayoutDetector if model not available.
    """
    def __init__(self):
        self._rule_based = RuleBasedLayoutDetector()
        self._model = None
        self._loaded = False

    def _load(self) -> bool:
        if self._loaded:
            return self._model is not None
        if not settings.LAYOUT_MODEL_PATH:
            self._loaded = True
            return False
        try:
            from transformers import AutoProcessor, AutoModelForObjectDetection
            self._processor = AutoProcessor.from_pretrained(settings.LAYOUT_MODEL_PATH)
            self._model     = AutoModelForObjectDetection.from_pretrained(settings.LAYOUT_MODEL_PATH)
            self._model.eval()
            self._loaded = True
            log.info("Layout model loaded", extra={"path": settings.LAYOUT_MODEL_PATH})
            return True
        except Exception as exc:
            log.error("Failed to load layout model", extra={"error": str(exc)})
            self._loaded = True
            return False

    def detect(self, image: np.ndarray) -> LayoutResult:
        if not self._load():
            return self._rule_based.detect(image)
        try:
            import torch
            from PIL import Image as PILImage
            pil = PILImage.fromarray(image[..., ::-1])  # BGR→RGB
            inputs = self._processor(images=pil, return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = self._model(**inputs)
            return self._rule_based.detect(image)  # fallback until model integrated
        except Exception as exc:
            log.error("Layout model inference failed", extra={"error": str(exc)})
            return self._rule_based.detect(image)


# ── Singleton ──────────────────────────────────────────────────────────────────

_detector: Optional[ModelBasedLayoutDetector] = None


def get_layout_detector() -> ModelBasedLayoutDetector:
    global _detector
    if _detector is None:
        _detector = ModelBasedLayoutDetector()
    return _detector