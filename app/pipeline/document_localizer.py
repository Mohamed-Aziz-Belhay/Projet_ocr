#document_localizer.py
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
    2. Tesseract OSD (Orientation and Script Detection) attempts to detect the
       real rotation angle (0/90/180/270) directly, before any ROI extraction.
       If OSD succeeds with sufficient confidence, a single, already-corrected
       candidate is produced -- avoiding four full ROI extractions followed by
       a fragile scoring comparison.
    3. If OSD fails or is not confident enough, we fall back to the original
       strategy: keep all four raw YOLO rotation candidates, unchanged.
    4. We also run DocumentNormalizer on the YOLO crop to produce deskewed
       candidates, for cases where the card is diagonally tilted rather than
       rotated by a multiple of 90 degrees.
    5. The downstream ROI/MRZ scorer chooses the best candidate among whatever
       this method returns.

    Why:
    - Passport already works well with raw YOLO crop.
    - svk_id rotated can fail because the card is diagonally tilted, not only 90/180 rotated.
    - Adding normalized candidates improves rotated ID cases without removing the stable raw candidates.
    - OSD-based correction (added after a diagnosed failure on a 180deg-rotated
      ID card, cf. rapport Ch.5) removes the ambiguity that let a wrong-rotation
      candidate outscore the correct one when several ROI fields happened to
      match valid-looking-but-duplicated values.
    """

    # Seuil de confiance OSD Tesseract en dessous duquel on ne fait pas confiance
    # à l'angle détecté et on retombe sur les 4 candidats de rotation existants.
    # Valeur de départ à calibrer empiriquement sur un échantillon réel.
    OSD_MIN_CONFIDENCE = 1.0

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

                osd_result = self._detect_osd_angle(crop)

                if osd_result is not None:
                    osd_angle, osd_confidence = osd_result
                    crop = self._rotate_by_angle(crop, osd_angle)

                    raw_candidates = [
                        {
                            "image": crop,
                            "angle": 0,
                            "candidate_index": 0,
                            "rotation_index": 0,
                            "source": "yolo_crop_osd_corrected",
                            "candidate": None,
                            "osd_detected_angle": osd_angle,
                            "osd_confidence": osd_confidence,
                        }
                    ]
                else:
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
                # Keep raw (or OSD-corrected) candidates first because they are
                # already stable for passports. Add normalized candidates after
                # them for skewed/rotated IDs.
                candidates = raw_candidates + normalized_candidates

                return LocalizedDocument(
                    image=crop,
                    candidates=candidates,
                    diagnostics={
                        "localizer": "document_localizer_v4_osd_plus_raw_plus_normalized",
                        "method": "yolo_crop",
                        "detector": detection.model_dump(),
                        "input_shape": list(image.shape[:2]),
                        "localized_shape": list(crop.shape[:2]),
                        "candidate_count": len(candidates),
                        "raw_candidate_count": len(raw_candidates),
                        "normalized_candidate_count": len(normalized_candidates),
                        "yolo_crop_padding": 0.02,
                        "osd_correction": {
                            "executed": osd_result is not None,
                            "detected_angle": osd_result[0] if osd_result else None,
                            "confidence": osd_result[1] if osd_result else None,
                            "min_confidence_required": self.OSD_MIN_CONFIDENCE,
                        },
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
                "localizer": "document_localizer_v4_osd_plus_raw_plus_normalized",
                "method": "opencv_fallback",
                "detector": detection.model_dump(),
                "input_shape": list(image.shape[:2]),
                "fallback_normalizer": normalized.diagnostics,
                "localized_shape": list(normalized.image.shape[:2]),
                "candidate_count": len(candidates),
            },
        )

    def _detect_osd_angle(self, image: np.ndarray) -> Optional[tuple[int, float]]:
        """
        Détecte l'orientation du document via Tesseract OSD (Orientation and
        Script Detection) -- une passe rapide de détection de mise en page,
        pas une reconnaissance complète, indépendante du moteur OCR principal
        utilisé ensuite pour l'extraction des champs.

        Retourne (angle_de_correction, confiance) si la détection est jugée
        assez fiable, sinon None -- dans ce cas l'appelant retombe sur les
        4 candidats de rotation existants (comportement d'origine, inchangé).
        """
        try:
            import pytesseract

            osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
            confidence = float(osd.get("orientation_conf", 0.0))

            if confidence < self.OSD_MIN_CONFIDENCE:
                return None

            angle = int(osd.get("rotate", 0)) % 360

            if angle not in (0, 90, 180, 270):
                return None

            return angle, confidence

        except Exception:
            return None

    @staticmethod
    def _rotate_by_angle(image: np.ndarray, angle: int) -> np.ndarray:
        rotation_map = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }

        if angle in rotation_map:
            return cv2.rotate(image, rotation_map[angle])

        return image

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