from __future__ import annotations

import cv2
import numpy as np

from app.pipeline.document_normalizer import DocumentNormalizer


def test_document_normalizer_returns_candidates_on_synthetic_card():
    page = np.full((600, 900, 3), 255, dtype=np.uint8)

    card = np.full((220, 360, 3), (60, 120, 180), dtype=np.uint8)
    cv2.rectangle(card, (30, 40), (180, 70), (255, 255, 255), -1)
    cv2.rectangle(card, (260, 60), (330, 160), (220, 220, 220), -1)

    page[180:400, 260:620] = card

    normalizer = DocumentNormalizer()
    result = normalizer.normalize(page, mode="balanced")

    assert result.image is not None
    assert result.image.size > 0
    assert result.diagnostics["found_contour"] is True
    assert len(result.candidates) >= 2
    assert 0 in [c["angle"] for c in result.candidates]
