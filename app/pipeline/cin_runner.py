from __future__ import annotations

from typing import Any, Dict, Optional
import numpy as np

# Keep the previous complete runner as the base implementation.
# If you replace app/pipeline/cin_runner.py with this file, rename the old file
# to app/pipeline/cin_runner_legacy.py and change this import accordingly:
# from app.pipeline.cin_runner_legacy import CINPipelineRunner as BaseCINPipelineRunner
from app.pipeline.cin_runner_legacy import CINPipelineRunner as BaseCINPipelineRunner

from app.services.cin_text_normalizer import CINTextNormalizer
from app.services.cin_field_parsers import CINFieldParsers
from app.services.cin_label_spatial_extractor import CINLabelSpatialExtractor
from app.services.cin_candidate_merger import CINCandidateMerger


class CINPipelineRunner(BaseCINPipelineRunner):
    """
    Final orchestrator.

    The base runner still handles:
    - preprocessing
    - ROI fallback
    - full OCR fallback
    - fusing
    - post-processing
    - business validation
    - response building

    This orchestrator overrides only the spatial-box stage and injects:
    - label-spatial extraction around اللقب / الاسم / تاريخ الولادة / مكانها
    - candidate merging
    """

    def __init__(self, *args: Any, **kwargs: Any):
        try:
            super().__init__(*args, **kwargs)
        except TypeError:
            # The existing runner may not define __init__.
            pass

        self.cin_normalizer = CINTextNormalizer()
        self.cin_parsers = CINFieldParsers(self.cin_normalizer)
        self.label_spatial_extractor = CINLabelSpatialExtractor(
            normalizer=self.cin_normalizer,
            parsers=self.cin_parsers,
        )
        self.candidate_merger = CINCandidateMerger(
            normalizer=self.cin_normalizer,
            parsers=self.cin_parsers,
        )

    def _run_spatial_boxes(
        self,
        *,
        engine_name: str,
        card_img: np.ndarray,
        spatial_debug: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        from app.services.cin_box_ocr import easyocr_boxes, paddle_boxes
        from app.services.cin_spatial_extractor import CINSpatialExtractor

        try:
            if engine_name == "easyocr_boxes":
                raw_boxes = easyocr_boxes(card_img)
            elif engine_name == "paddle_boxes":
                raw_boxes = paddle_boxes(card_img)
            else:
                spatial_debug.setdefault("errors", []).append(f"unknown: {engine_name}")
                return None

            if not raw_boxes:
                spatial_debug.setdefault("trials", []).append({
                    "engine": engine_name,
                    "box_count": 0,
                    "fields": {},
                    "scores": {},
                    "meta": {},
                    "label_spatial": {},
                    "raw_text_reconstructed": "",
                })
                return None

            raw_text_from_boxes = self._reconstruct_text_from_boxes(raw_boxes)

            # Existing extractor.
            spatial = CINSpatialExtractor().extract(raw_boxes)

            # New robust extractor: local OCR context around labels.
            label_spatial = self.label_spatial_extractor.extract(raw_boxes)

            # Merge both sources into the standard spatial result format.
            merged = self.candidate_merger.merge_label_spatial_into_spatial(spatial, label_spatial)

            trial = self._spatial_result_to_trial(merged, engine_name)

            spatial_debug.setdefault("trials", []).append({
                "engine": engine_name,
                "box_count": len(raw_boxes),
                "fields": merged.get("fields", {}),
                "scores": merged.get("scores", {}),
                "meta": merged.get("meta", {}),
                "label_spatial": label_spatial,
                "raw_text_reconstructed": raw_text_from_boxes,
            })

            if trial:
                trial["raw_text"] = raw_text_from_boxes
                trial.setdefault("diagnostics", {})
                trial["diagnostics"]["label_spatial"] = label_spatial

            return trial

        except Exception as exc:
            spatial_debug.setdefault("errors", []).append(f"{engine_name}: {exc}")
            return None
