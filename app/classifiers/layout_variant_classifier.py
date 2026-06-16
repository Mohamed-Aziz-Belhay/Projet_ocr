"""
app/classifiers/layout_variant_classifier.py
Phase-D: within a doc_family, distinguish layout variants.
Example: registre_commerce has "modern" and "legacy_ar" variants.

Strategy:
  1. Detect print orientation (portrait/landscape)
  2. Detect dominant text direction (LTR / RTL)
  3. Match against variant fingerprints (keyword anchors + structural cues)
  4. Return (variant_id, confidence)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class VariantFingerprint:
    """Describes the textual signature of a layout variant."""
    variant_id: str
    doc_family: str
    required_anchors: List[str] = field(default_factory=list)   # must ALL be present
    bonus_anchors: List[str] = field(default_factory=list)      # each hit adds score
    dominant_script: Optional[str] = None                       # "arabic" | "latin" | None
    min_score: float = 0.5


# ââ Variant registry âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Add new variants here without touching the classifier logic.

_FINGERPRINTS: List[VariantFingerprint] = [
    VariantFingerprint(
        variant_id="registre_commerce_modern",
        doc_family="business_registry",
        required_anchors=["registre de commerce", "forme juridique"],
        bonus_anchors=["capital social", "siÃ¨ge social", "dÃ©nomination"],
        dominant_script="latin",
    ),
    VariantFingerprint(
        variant_id="registre_commerce_legacy_ar",
        doc_family="business_registry",
        required_anchors=["Ø§ÙØ³Ø¬Ùýÿýÿýÿýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿ Ø§ÙÙØ§ÙÙÙÙ"],
        bonus_anchors=["Ø±Ø£Ø³ýÿýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿ± Ø§ÙØ§Ø¬ØªÙØ§Ø¹Ù", "Ø§ÙØªØ³ÙÙØ©"],
        dominant_script="arabic",
    ),
    VariantFingerprint(
        variant_id="cin_tn_recto",
        doc_family="id_document",
        required_anchors=["carte d'identitÃ© nationale", "tunisienne"],
        bonus_anchors=["date de naissance", "lieu de naissance"],
        dominant_script="latin",
        min_score=0.4,
    ),
    VariantFingerprint(
        variant_id="cin_tn_recto_ar",
        doc_family="id_document",
        required_anchors=["Ø¨Ø·Ø§ÙØ©ýÿýÿýÿýÿýÿýÿýÿ Ø§ÙÙØ·ÙÙØ©"],
        bonus_anchors=["ØªØ§Ø±ÙØ®ýÿýÿýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿ Ø§ÙÙÙØ§Ø¯Ø©"],
        dominant_script="arabic",
        min_score=0.4,
    ),
    VariantFingerprint(
        variant_id="invoice_modern",
        doc_family="invoice",
        required_anchors=["facture"],
        bonus_anchors=["total ttc", "tva", "ht", "montant"],
        min_score=0.3,
    ),
]


# ââ Script detection âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]{3,}")
_LATIN_RE = re.compile(r"[a-zA-ZÃ-Ã¿]{3,}")


def _detect_dominant_script(text: str) -> str:
    arabic = len(_ARABIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if arabic > latin:
        return "arabic"
    if latin > 0:
        return "latin"
    return "unknown"


# ââ Orientation (heuristic via aspect ratio) ââââââââââââââââââââââââââââââââââ

def _detect_orientation(image: np.ndarray) -> str:
    h, w = image.shape[:2]
    return "landscape" if w > h else "portrait"


# ââ Main classifier ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def classify_variant(
    doc_family: str,
    text: str,
    image: Optional[np.ndarray] = None,
) -> Tuple[str, float]:
    """
    Returns (variant_id, confidence).
    Falls back to (doc_family + "_generic", 0.0) if nothing matches.
    """
    text_lower = text.lower()
    dominant_script = _detect_dominant_script(text)

    candidates = [fp for fp in _FINGERPRINTS if fp.doc_family == doc_family]
    if not candidates:
        return f"{doc_family}_generic", 0.0

    best_variant: Optional[str] = None
    best_score: float = 0.0

    for fp in candidates:
        # Required anchors: all must be present
        if not all(a.lower() in text_lower for a in fp.required_anchors):
            continue

        score = 0.5  # base for passing required
        # Bonus anchors
        bonus_hits = sum(1 for a in fp.bonus_anchors if a.lower() in text_lower)
        if fp.bonus_anchors:
            score += 0.4 * (bonus_hits / len(fp.bonus_anchors))
        # Script alignment
        if fp.dominant_script and fp.dominant_script == dominant_script:
            score += 0.1

        if score >= fp.min_score and score > best_score:
            best_score = score
            best_variant = fp.variant_id

    if best_variant:
        log.debug(
            "Layout variant detected",
            extra={"variant": best_variant, "score": round(best_score, 3)},
        )
        return best_variant, round(best_score, 3)

    return f"{doc_family}_generic", 0.0