from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from app.models.document_detector.predictor import DetectionResult, get_document_detector
from app.pipeline.document_normalizer import DocumentNormalizer


@dataclass
class LocalizedDocument:
    image: np.ndarray
    candidates: List[Dict[str, Any]]
    diagnostics: Dict[str, Any]


class DocumentLocalizer:
    """
    Document localization layer.

    Strategy:
    1. YOLO detects the document/card/passport area.
    2. We keep raw YOLO rotation candidates.
    3. We also run DocumentNormalizer on the YOLO crop to produce deskewed candidates.
    4. The downstream ROI/MRZ scorer chooses the best candidate.

    Why:
    - Passport already works well with raw YOLO crop.
    - svk_id rotated can fail because the card is diagonally tilted, not only 90/180 rotated.
    - Adding normalized candidates improves rotated ID cases without removing the stable raw candidates.
    """

    def __init__(self):
        self.detector = get_document_detector()
        self.fallback_normalizer = DocumentNormalizer()

    def localize(
        self,
        image: np.ndarray,
        mode: str = "balanced",
    ) -> LocalizedDocument:
        if image is None or image.size == 0:
            raise ValueError("Empty image passed to DocumentLocalizer")

        detection = self.detector.detect(image)

        if detection.found and detection.bbox_xyxy:
            crop = self._crop_from_detection(
                image=image,
                detection=detection,
                pad_ratio=0.02,
            )

            if crop is not None and crop.size > 0:
                crop = self._force_landscape(crop)

                raw_candidates = self._rotation_candidates(
                    crop,
                    source="yolo_crop_raw",
                    candidate_index_base=0,
                )

                normalized_candidates: List[Dict[str, Any]] = []
                normalizer_diagnostics: Dict[str, Any] = {
                    "executed": False,
                    "reason": "not_run",
                }

                try:
                    normalized = self.fallback_normalizer.normalize(
                        crop,
                        mode=mode,
                        enable_rotation_candidates=True,
                    )

                    normalizer_diagnostics = {
                        "executed": True,
                        "diagnostics": normalized.diagnostics,
                        "normalized_shape": list(normalized.image.shape[:2]),
                        "candidate_count": len(normalized.candidates),
                    }

                    normalized_candidates = self._normalize_candidate_metadata(
                        normalized.candidates,
                        source_prefix="yolo_crop_normalized",
                        candidate_index_base=100,
                    )

                except Exception as exc:
                    normalizer_diagnostics = {
                        "executed": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }

                # Important:
                # Keep raw candidates first because they are already stable for passports.
                # Add normalized candidates after them for skewed/rotated IDs.
                candidates = raw_candidates + normalized_candidates

                return LocalizedDocument(
                    image=crop,
                    candidates=candidates,
                    diagnostics={
                        "localizer": "document_localizer_v3_yolo_raw_plus_normalized",
                        "method": "yolo_crop",
                        "detector": detection.model_dump(),
                        "input_shape": list(image.shape[:2]),
                        "localized_shape": list(crop.shape[:2]),
                        "candidate_count": len(candidates),
                        "raw_candidate_count": len(raw_candidates),
                        "normalized_candidate_count": len(normalized_candidates),
                        "yolo_crop_padding": 0.02,
                        "aspect_trim": {
                            "enabled": False,
                            "reason": "aspect trimming degraded ROI alignment in previous tests",
                        },
                        "post_yolo_normalizer": normalizer_diagnostics,
                    },
                )

        # Fallback when YOLO fails.
        normalized = self.fallback_normalizer.normalize(
            image,
            mode=mode,
            enable_rotation_candidates=True,
        )

        candidates = self._normalize_candidate_metadata(
            normalized.candidates,
            source_prefix="opencv_fallback_normalized",
            candidate_index_base=0,
        )

        return LocalizedDocument(
            image=normalized.image,
            candidates=candidates,
            diagnostics={
                "localizer": "document_localizer_v3_yolo_raw_plus_normalized",
                "method": "opencv_fallback",
                "detector": detection.model_dump(),
                "input_shape": list(image.shape[:2]),
                "fallback_normalizer": normalized.diagnostics,
                "localized_shape": list(normalized.image.shape[:2]),
                "candidate_count": len(candidates),
            },
        )

    def _crop_from_detection(
        self,
        *,
        image: np.ndarray,
        detection: DetectionResult,
        pad_ratio: float = 0.02,
    ) -> Optional[np.ndarray]:
        if not detection.bbox_xyxy:
            return None

        h, w = image.shape[:2]
        x1, y1, x2, y2 = [float(v) for v in detection.bbox_xyxy]

        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)

        x1 -= bw * pad_ratio
        y1 -= bh * pad_ratio
        x2 += bw * pad_ratio
        y2 += bh * pad_ratio

        x1 = int(max(0, round(x1)))
        y1 = int(max(0, round(y1)))
        x2 = int(min(w, round(x2)))
        y2 = int(min(h, round(y2)))

        if x2 <= x1 or y2 <= y1:
            return None

        crop = image[y1:y2, x1:x2]

        if crop is None or crop.size == 0:
            return None

        return crop

    def _force_landscape(
        self,
        image: np.ndarray,
    ) -> np.ndarray:
        h, w = image.shape[:2]

        if h > w:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)

        return image

    def _rotation_candidates(
        self,
        image: np.ndarray,
        source: str,
        candidate_index_base: int = 0,
    ) -> List[Dict[str, Any]]:
        return [
            {
                "image": image,
                "angle": 0,
                "candidate_index": candidate_index_base,
                "rotation_index": 0,
                "source": source,
                "candidate": None,
            },
            {
                "image": cv2.rotate(image, cv2.ROTATE_180),
                "angle": 180,
                "candidate_index": candidate_index_base,
                "rotation_index": 1,
                "source": source,
                "candidate": None,
            },
            {
                "image": cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
                "angle": 90,
                "candidate_index": candidate_index_base,
                "rotation_index": 2,
                "source": source,
                "candidate": None,
            },
            {
                "image": cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
                "angle": 270,
                "candidate_index": candidate_index_base,
                "rotation_index": 3,
                "source": source,
                "candidate": None,
            },
        ]

    def _normalize_candidate_metadata(
        self,
        candidates: List[Dict[str, Any]],
        source_prefix: str,
        candidate_index_base: int,
    ) -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []

        for idx, candidate in enumerate(candidates or []):
            item = dict(candidate)

            old_source = str(item.get("source") or "candidate")
            item["source"] = f"{source_prefix}:{old_source}"
            item["candidate_index"] = candidate_index_base + idx

            if "rotation_index" not in item:
                item["rotation_index"] = idx

            if "angle" not in item:
                item["angle"] = item.get("rotation", 0)

            output.append(item)

        return output