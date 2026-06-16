from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.pipeline.common import call_recognize_document, get_engine_adapter, normalize_text
from app.services.midv_field_normalizer import normalize_midv_field


VISUAL_FIELDS = {"photo", "face", "signature"}


class ROITemplateExtractionService:
    def _get(self, obj: Any, key: str, default=None):
        if obj is None:
            return default

        if isinstance(obj, dict):
            return obj.get(key, default)

        return getattr(obj, key, default)

    def _template_id(self, template: Any) -> str:
        template_id = self._get(template, "id", None)

        if not template_id:
            template_id = self._get(template, "template_id", "")

        return str(template_id or "")

    def _source_class(self, template: Any) -> str:
        return str(self._get(template, "source_class", "") or "")

    def _is_midv_template(self, template: Any) -> bool:
        return self._template_id(template).startswith("midv_")

    def _expected_nationality(self, template: Any) -> Optional[str]:
        """
        Expected nationality by MIDV class/template.

        This avoids accepting OCR garbage such as:
        - GTT for midv_svk_id
        - CVV for midv_svk_id
        - random 3-letter codes
        """

        template_id = self._template_id(template).lower()
        source_class = self._source_class(template).lower()

        haystack = f"{template_id} {source_class}"

        if "svk" in haystack:
            return "SVK"

        if "aze" in haystack:
            return "AZE"

        if "srb" in haystack:
            return "SRB"

        return None

    def _roi_fields(self, template: Any) -> List[Dict[str, Any]]:
        roi_fields = self._get(template, "roi_fields", []) or []
        out: List[Dict[str, Any]] = []

        for item in roi_fields:
            if isinstance(item, dict):
                out.append(item)
                continue

            try:
                out.append(item.model_dump())
            except Exception:
                out.append(
                    {
                        "name": getattr(item, "name", None),
                        "type": getattr(item, "type", "text"),
                        "bbox_norm": getattr(item, "bbox_norm", None),
                        "output_key": getattr(item, "output_key", None),
                        "required": getattr(item, "required", False),
                        "visual": getattr(item, "visual", False),
                        "orientation": getattr(item, "orientation", "0"),
                    }
                )

        return [x for x in out if x.get("name") and x.get("bbox_norm")]

    def _prepare_roi_for_ocr(self, crop: np.ndarray) -> np.ndarray:
        if crop is None or crop.size == 0:
            return crop

        h, w = crop.shape[:2]

        # Stable MIDV setting:
        # ROI are often small after YOLO crop, so upscale enough for OCR.
        min_h = 96
        min_w = 220

        scale_h = min_h / max(h, 1)
        scale_w = min_w / max(w, 1)
        scale = max(scale_h, scale_w, 1.0)

        if scale > 1.0:
            new_w = int(round(w * scale))
            new_h = int(round(h * scale))
            crop = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        if len(crop.shape) == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop

        # Light contrast enhancement, no aggressive binarization.
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    def _crop_norm(
        self,
        image: np.ndarray,
        bbox_norm: List[float],
        pad_x_min: float = 0.018,
        pad_y_min: float = 0.025,
    ) -> Optional[np.ndarray]:
        if image is None or image.size == 0:
            return None

        h, w = image.shape[:2]

        try:
            x, y, bw, bh = [float(v) for v in bbox_norm]
        except Exception:
            return None

        if bw <= 0 or bh <= 0:
            return None

        # Stable broad padding.
        # This was more stable than field-specific padding on rotated scans.
        pad_x = max(bw * 0.22, pad_x_min)
        pad_y = max(bh * 0.55, pad_y_min)

        x1 = int(max(0, (x - pad_x) * w))
        y1 = int(max(0, (y - pad_y) * h))
        x2 = int(min(w, (x + bw + pad_x) * w))
        y2 = int(min(h, (y + bh + pad_y) * h))

        if x2 <= x1 or y2 <= y1:
            return None

        crop = image[y1:y2, x1:x2]

        if crop is None or crop.size == 0:
            return None

        return self._prepare_roi_for_ocr(crop.copy())

    def _rotate_if_needed(self, crop: np.ndarray, orientation: str) -> np.ndarray:
        orientation = str(orientation or "0").strip()

        if orientation == "90":
            return cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)

        if orientation == "-90":
            return cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)

        if orientation in {"180", "-180"}:
            return cv2.rotate(crop, cv2.ROTATE_180)

        return crop

    def _normalize_date(self, value: str) -> Optional[str]:
        if not value:
            return None

        value = value.strip().replace("  ", " ")

        m = re.search(r"(\d{1,2})[.\-/ ]+(\d{1,2})[.\-/ ]+(\d{2,4})", value)

        if m:
            d, mo, y = m.groups()
            y = int(y)

            if y < 100:
                y = 2000 + y if y < 30 else 1900 + y

            try:
                from datetime import datetime

                dt = datetime(y, int(mo), int(d))
            except ValueError:
                return None

            return dt.strftime("%Y-%m-%d")

        m = re.search(r"(\d{4})[.\-/ ]+(\d{1,2})[.\-/ ]+(\d{1,2})", value)

        if m:
            y, mo, d = m.groups()

            try:
                from datetime import datetime

                dt = datetime(int(y), int(mo), int(d))
            except ValueError:
                return None

            return dt.strftime("%Y-%m-%d")

        return value

    def _normalize_value(self, value: str, field_type: str) -> Optional[str]:
        value = normalize_text(value)

        if not value:
            return None

        if field_type == "date":
            return self._normalize_date(value)

        if field_type == "mrz":
            return re.sub(r"\s+", "", value.upper())

        return value

    def _validate(
        self,
        value: Optional[str],
        field: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        required = bool(field.get("required", False))
        field_type = field.get("type", "text")

        if not value:
            if required:
                return False, "required field missing"

            return False, "field not found"

        if field_type == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False, "invalid date"

        if field_type == "mrz" and len(value) < 20:
            return False, "MRZ too short"

        regex = (field.get("validation", {}) or {}).get("regex")

        if regex and not re.search(regex, value):
            return False, "regex validation failed"

        return True, None

    def _effective_language_hints(
        self,
        *,
        template: Any,
        language_hints: List[str],
    ) -> List[str]:
        # MIDV = latin documents. Avoid Arabic OCR noise.
        if self._is_midv_template(template):
            return ["en"]

        clean_hints = [
            str(x).strip().lower()
            for x in (language_hints or [])
            if str(x).strip() and str(x).strip().lower() != "auto"
        ]

        return clean_hints or ["en"]

    def _recognize_crop(
        self,
        *,
        engine,
        crop: np.ndarray,
        language_hints: List[str],
    ) -> Tuple[str, float]:
        raw_text, score = call_recognize_document(engine, crop, language_hints)
        raw_text = normalize_text(raw_text or "")
        return raw_text, float(score or 0.0)

    def _safe_debug_name(self, name: str) -> str:
        name = str(name or "roi").strip()
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        return name or "roi"

    def _debug_save_crop(
        self,
        *,
        name: str,
        crop: Optional[np.ndarray],
    ) -> Optional[str]:
        if crop is None or crop.size == 0:
            return None

        try:
            from pathlib import Path

            debug_dir = Path("debug_roi")
            debug_dir.mkdir(exist_ok=True)

            safe_name = self._safe_debug_name(name)
            path = debug_dir / f"{safe_name}.jpg"

            cv2.imwrite(str(path), crop)

            return str(path).replace("\\", "/")

        except Exception:
            return None

    def extract(
        self,
        *,
        image: np.ndarray,
        template: Any,
        engine_name: str,
        language_hints: List[str],
        debug_prefix: str = "",
        content_bbox_norm: Optional[List[float]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        """
        Stable ROI extraction.

        Corrections:
        - debug_prefix prevents overwriting ROI crops between rotation candidates.
        - content_bbox_norm is accepted for compatibility with generic_runner.
        - content_bbox_norm is not applied here to avoid changing stable ROI geometry.
        - expected_nationality is passed to MIDV normalizer.
        - This prevents values like GTT/CVV being validated for midv_svk_id.
        """

        roi_fields = self._roi_fields(template)

        if not roi_fields:
            return [], {}, {"roi_extraction": "no_roi_fields"}, []

        engine = get_engine_adapter(engine_name)

        template_id = self._template_id(template)
        source_class = self._source_class(template)
        is_midv = self._is_midv_template(template)
        expected_nationality = self._expected_nationality(template)

        effective_language_hints = self._effective_language_hints(
            template=template,
            language_hints=language_hints,
        )

        fields: List[Dict[str, Any]] = []
        normalized: Dict[str, Any] = {}
        warnings: List[str] = []

        debug: Dict[str, Any] = {
            "roi_extraction": "template_roi_v3_expected_nationality_strict_midv",
            "engine": engine_name,
            "language_hints": effective_language_hints,
            "template_id": template_id,
            "source_class": source_class,
            "expected_nationality": expected_nationality,
            "debug_prefix": debug_prefix,
            "content_bbox_norm_received": content_bbox_norm,
            "content_bbox_norm_applied": False,
            "fields": [],
        }

        for field in roi_fields:
            name = field["name"]
            field_type = field.get("type", "text")
            output_key = field.get("output_key") or name
            name_lower = str(name).lower()
            is_visual = bool(field.get("visual")) or name_lower in VISUAL_FIELDS

            if is_visual:
                debug["fields"].append(
                    {
                        "name": name,
                        "output_key": output_key,
                        "skipped": True,
                        "reason": "visual field",
                        "bbox_norm": field.get("bbox_norm"),
                    }
                )
                continue

            crop = self._crop_norm(
                image=image,
                bbox_norm=field["bbox_norm"],
            )

            raw_text: Optional[str] = None
            value: Optional[str] = None
            score = 0.0
            valid = False
            error: Optional[str] = None
            debug_file: Optional[str] = None

            if crop is None:
                error = "invalid roi crop"

            else:
                crop = self._rotate_if_needed(
                    crop,
                    field.get("orientation", "0"),
                )

                debug_name = f"{debug_prefix}_{name}" if debug_prefix else name
                debug_file = self._debug_save_crop(name=debug_name, crop=crop)

                raw_text, score = self._recognize_crop(
                    engine=engine,
                    crop=crop,
                    language_hints=effective_language_hints,
                )

                if is_midv:
                    value, valid, error = normalize_midv_field(
                        field_name=name,
                        raw_text=raw_text,
                        expected_nationality=expected_nationality,
                    )

                    if field.get("required") and not valid and not error:
                        error = "required field missing"

                else:
                    value = self._normalize_value(raw_text, field_type)
                    valid, error = self._validate(value, field)

            confidence = 0.0

            if valid:
                confidence = max(0.70, min(float(score or 0.0), 0.95))

            elif value:
                confidence = 0.45

            review_required = bool(field.get("required", False) and not valid)

            field_result = {
                "name": name,
                "value": value,
                "confidence": round(confidence, 3),
                "validated": valid,
                "raw_text": raw_text,
                "raw_template_field": name,
                "error": error,
                "selected_engine": engine_name,
                "selected_source": "template_roi",
                "review_required": review_required,
                "reasons": ["selected_from:template_roi"] if value else ["field unresolved"],
            }

            fields.append(field_result)

            if valid and value is not None:
                normalized[output_key] = value

            debug["fields"].append(
                {
                    "name": name,
                    "output_key": output_key,
                    "raw_text": raw_text,
                    "value": value,
                    "valid": valid,
                    "error": error,
                    "bbox_norm": field.get("bbox_norm"),
                    "confidence": round(confidence, 3),
                    "debug_file": debug_file,
                    "review_required": review_required,
                }
            )

            if field.get("required") and not valid:
                warnings.append(f"Required ROI field '{name}' missing or invalid")

        return fields, normalized, debug, warnings


@lru_cache(maxsize=1)
def get_roi_template_extraction_service() -> ROITemplateExtractionService:
    return ROITemplateExtractionService()