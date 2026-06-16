from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np


class CINPreprocessor:
    """Lightweight preprocessor for Tunisian CIN images.

    Returns a dict with at least:
      - original
      - oriented
      - card_roi
      - gray
      - binary
    """

    def _deskew(self, image: np.ndarray) -> Tuple[np.ndarray, bool]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(th < 255))
        if len(coords) < 200:
            return image, False
        rect = cv2.minAreaRect(coords.astype(np.float32))
        angle = rect[-1]
        if angle < -45:
            angle = 90 + angle
        elif angle > 45:
            angle = angle - 90
        if abs(angle) < 1.0 or abs(angle) > 15:
            return image, False
        h, w = image.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated, True

    def _detect_card_roi(self, image: np.ndarray) -> Optional[np.ndarray]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = image.shape[:2]
        best = None
        best_area = 0
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = bw * bh
            if area < 0.25 * w * h:
                continue
            ratio = bw / max(bh, 1)
            if 1.2 <= ratio <= 2.5 and area > best_area:
                best_area = area
                best = (x, y, bw, bh)
        if not best:
            return None
        x, y, bw, bh = best
        pad_x = int(0.02 * bw)
        pad_y = int(0.03 * bh)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(w, x + bw + pad_x)
        y2 = min(h, y + bh + pad_y)
        roi = image[y1:y2, x1:x2].copy()
        return roi if roi.size else None

    def preprocess_cin(self, image: np.ndarray) -> Dict[str, Any]:
        original = image.copy()
        oriented, deskewed = self._deskew(original)
        card_roi = self._detect_card_roi(oriented)
        if card_roi is None or getattr(card_roi, "size", 0) == 0:
            card_roi = oriented.copy()

        gray = cv2.cvtColor(card_roi, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)

        return {
            "original": original,
            "oriented": oriented,
            "card_roi": card_roi,
            "gray": gray,
            "binary": binary,
            "deskewed": deskewed,
        }
