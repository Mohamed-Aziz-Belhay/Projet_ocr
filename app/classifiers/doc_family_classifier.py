#doc_family classifier based on swin and keyword heuristics
from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _keyword_score(text: str, keywords: list[str]) -> float:
    if not text:
        return 0.0

    low = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in low)

    return min(1.0, hits / max(1, min(len(keywords), 5)))


def classify_document(
    image_path: Optional[str] = None,
    image: Optional[Any] = None,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Classification order:
    1. Swin on localized image array when available
    2. Swin on image path
    3. Keyword fallback on OCR text
    """

    try:
        from app.models.swin.predictor import get_swin_document_classifier

        clf = get_swin_document_classifier()

        if image is not None:
            pred = clf.predict_array(image)

            if pred.get("available") and pred.get("confidence", 0.0) >= 0.65:
                return pred

        if image_path:
            pred = clf.predict_image_path(image_path)

            if pred.get("available") and pred.get("confidence", 0.0) >= 0.65:
                return pred

    except Exception:
        pass

    text = text or ""

    candidates = [
        {
            "document_type": "invoice",
            "template_id": "invoice_tn",
            "keywords": [
                "facture",
                "invoice",
                "total",
                "montant",
                "tva",
                "ttc",
                "ht",
            ],
        },
        {
            "document_type": "passport",
            "template_id": "passport_generic",
            "keywords": [
                "passport",
                "passeport",
                "surname",
                "given names",
                "nationality",
                "date of expiry",
                "mrz",
            ],
        },
        {
            "document_type": "registre_commerce",
            "template_id": "registre_commerce",
            "keywords": [
                "registre de commerce",
                "rc",
                "raison sociale",
                "matricule fiscal",
                "identifiant fiscal",
                "commerce",
            ],
        },
        {
            "document_type": "cin_tn",
            "template_id": "cin_tn",
            "keywords": [
                "بطاقة التعريف",
                "الجمهورية التونسية",
                "اللقب",
                "الاسم",
                "تاريخ الولادة",
            ],
        },
    ]

    best = {
        "document_type": "custom",
        "template_id": None,
        "confidence": 0.0,
        "method": "unknown_fallback",
    }

    for candidate in candidates:
        score = _keyword_score(text, candidate["keywords"])

        if candidate["document_type"] == "passport":
            if re.search(r"P<[A-Z]{3}", text.upper()):
                score = max(score, 0.85)

        if score > best["confidence"]:
            best = {
                "document_type": candidate["document_type"],
                "template_id": candidate["template_id"],
                "confidence": round(score, 3),
                "method": "keyword_fallback",
            }

    if best["confidence"] < 0.20:
        return {
            "document_type": "custom",
            "template_id": None,
            "confidence": best["confidence"],
            "method": "unknown_fallback",
        }

    return best