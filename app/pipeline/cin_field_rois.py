# cin_field_rois.py - Define ROIs for Tunisian CIN fields and extract them from card images
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np


@dataclass(frozen=True)
class RoiBox:
    """Normalized ROI coordinates in [0,1]."""

    x1: float
    y1: float
    x2: float
    y2: float

    def clamp(self) -> "RoiBox":
        def c(v: float) -> float:
            return max(0.0, min(1.0, float(v)))
        return RoiBox(c(self.x1), c(self.y1), c(self.x2), c(self.y2))


def crop_norm(image_bgr: np.ndarray, box: RoiBox) -> Optional[np.ndarray]:
    if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return None

    h, w = image_bgr.shape[:2]
    b = box.clamp()

    x1 = int(b.x1 * w)
    x2 = int(b.x2 * w)
    y1 = int(b.y1 * h)
    y2 = int(b.y2 * h)

    if x2 <= x1 or y2 <= y1:
        return None

    roi = image_bgr[y1:y2, x1:x2]
    if roi is None or getattr(roi, "size", 0) == 0:
        return None
    return roi


def _get_cin_boxes(card_bgr: np.ndarray) -> Dict[str, RoiBox]:
    """
    Tuned ROIs for Tunisian CIN after card crop/orientation.

    Design goals:
    - strong numeric ROI for CIN number
    - narrow row ROIs for text fields
    - reduced overlap between family / first / date / place
    """

    h, w = card_bgr.shape[:2]
    aspect = w / max(h, 1)

    if aspect >= 1.45:
        return {
            "cin_number": RoiBox(0.34, 0.22, 0.90, 0.34),
            "family_name": RoiBox(0.46, 0.37, 0.95, 0.47),
            "first_name": RoiBox(0.46, 0.48, 0.95, 0.58),
            "date_of_birth": RoiBox(0.46, 0.60, 0.95, 0.71),
            "place_of_birth": RoiBox(0.46, 0.73, 0.95, 0.84),
            "gender": RoiBox(0.46, 0.84, 0.70, 0.92),
            "right_text_block": RoiBox(0.42, 0.34, 0.97, 0.90),
        }

    return {
        "cin_number": RoiBox(0.32, 0.21, 0.90, 0.34),
        "family_name": RoiBox(0.44, 0.36, 0.96, 0.47),
        "first_name": RoiBox(0.44, 0.48, 0.96, 0.59),
        "date_of_birth": RoiBox(0.44, 0.61, 0.96, 0.73),
        "place_of_birth": RoiBox(0.44, 0.75, 0.96, 0.87),
        "gender": RoiBox(0.44, 0.86, 0.70, 0.94),
        "right_text_block": RoiBox(0.40, 0.34, 0.97, 0.92),
    }


def extract_cin_field_rois(card_bgr: np.ndarray) -> Dict[str, np.ndarray]:
    """
    Return field ROIs for Tunisian CIN.

    Expected input:
    - oriented / cropped CIN card image

    Output keys:
    - cin_number
    - family_name
    - first_name
    - date_of_birth
    - place_of_birth
    - gender
    - right_text_block
    """
    if card_bgr is None or getattr(card_bgr, "size", 0) == 0:
        return {}

    boxes = _get_cin_boxes(card_bgr)

    out: Dict[str, np.ndarray] = {}
    for name, box in boxes.items():
        roi = crop_norm(card_bgr, box)
        if roi is not None and getattr(roi, "size", 0) > 0:
            out[name] = roi
    return out