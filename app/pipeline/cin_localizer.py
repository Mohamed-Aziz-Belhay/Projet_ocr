"""
app/pipeline/cin_localizer.py
CIN-specific field localization using fixed zones with optional anchor-based refinement.
"""
from __future__ import annotations
from typing import Dict, Iterable, List, Tuple

from app.engines.engine_factory import OCRToken
from app.utils.rtl_text import cleanup_arabic_text


DEFAULT_FIXED_ZONES: Dict[str, List[float]] = {
    "id_number": [0.34, 0.16, 0.74, 0.34],
    "last_name": [0.56, 0.22, 0.98, 0.42],
    "first_name": [0.56, 0.34, 0.98, 0.54],
    "birth_date": [0.53, 0.48, 0.98, 0.70],
    "birth_place": [0.54, 0.64, 0.98, 0.93],
    "right_text": [0.50, 0.18, 0.98, 0.96],
}

ANCHOR_MAP = {
    "last_name": [ýÿýÿýÿýÿýÿ¨"],
    "first_name": [ýÿýÿýÿýÿýÿ"],
    "birth_date": [ýÿýÿýÿýÿýÿ®", ýÿýÿýÿýÿýÿýÿýÿ©"],
    "birth_place": ["ÙÙØ§ÙÙØ§", "ÙÙØ§Ù", "Ø§ÙÙÙØ§Ø¯Ø©"],
}


def get_fixed_zones(image_shape: Tuple[int, int, int], template=None) -> Dict[str, List[float]]:
    zones = dict(DEFAULT_FIXED_ZONES)
    if template and getattr(template, "fixed_zones", None):
        zones.update(template.fixed_zones)
    return zones


def crop_zone(image, zone: List[float]):
    from app.engines.engine_factory import BaseOCREngine
    return BaseOCREngine._crop_zone(image, zone)


def refine_zones_from_anchors(
    zones: Dict[str, List[float]],
    tokens: Iterable[OCRToken],
    image_shape: Tuple[int, int, int],
) -> Dict[str, List[float]]:
    h, w = image_shape[:2]
    refined = dict(zones)
    tokens = list(tokens or [])
    if not tokens:
        return refined

    normalized = [cleanup_arabic_text(t.text) for t in tokens]
    for field_name, anchors in ANCHOR_MAP.items():
        for token, norm in zip(tokens, normalized):
            if any(a in norm for a in anchors):
                x, y, bw, bh = token.bbox
                x1 = max(0, x - int(0.05 * w))
                y1 = max(0, y - int(0.02 * h))
                x2 = min(w, x + bw + int(0.30 * w))
                y2 = min(h, y + bh + int(0.12 * h))
                refined[field_name] = [x1, y1, x2, y2]
                break
    return refined


def build_cin_zones(image_shape: Tuple[int, int, int], template=None, tokens=None) -> Dict[str, List[float]]:
    zones = get_fixed_zones(image_shape, template)
    if tokens:
        zones = refine_zones_from_anchors(zones, tokens, image_shape)
    return zones