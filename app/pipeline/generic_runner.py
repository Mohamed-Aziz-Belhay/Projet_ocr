"""
app/pipeline/generic_runner.py

Generic template-driven OCR pipeline.

Architecture:
- localize document with YOLO/document localizer first
- classify localized crop with Swin/family classifier
- resolve template
- passport: MRZ-first extraction
- invoice: OCR global + invoice rules extractor
- other templates: ROI OCR on localized candidates
- fast mode returns ROI/MRZ result without full OCR when possible
- balanced/full can fallback to raw OCR text extraction
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.extractors.invoice_extractor import InvoiceExtractor
from app.extractors.registre_commerce_extractor import (
    RegistreCommerceExtractor,
    merge_registre_fields,
)
from app.pipeline.common import load_image, call_recognize_document, get_engine_adapter, normalize_text
from app.pipeline.document_localizer import DocumentLocalizer
from app.pipeline.document_normalizer import DocumentNormalizer
from app.pipeline.lang_detect import detect_language
try:
    from app.pipeline.rne_layout_extractor import run_rne_layout_ocr
except Exception:
    run_rne_layout_ocr = None
try:
    from app.pipeline.invoice_targeted_ocr import run_invoice_targeted_ocr
except Exception:
    run_invoice_targeted_ocr = None
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.services.generic_extraction_service import get_generic_extraction_service
from app.services.passport_extraction_service import get_passport_extraction_service
from app.services.roi_template_extraction_service import get_roi_template_extraction_service
from app.services.template_service import get_template_service

log = get_logger(__name__)

def _safe_image_width(image) -> int | None:
    """
    Retourne la largeur d'une image numpy/OpenCV.
    Image shape attendue: (H, W) ou (H, W, C).
    """
    if image is None:
        return None

    shape = getattr(image, "shape", None)

    if not shape or len(shape) < 2:
        return None

    return int(shape[1])


def _passport_crop_width_ratio(original_image, localized_image) -> float | None:
    """
    Compare la largeur du crop utilisé avec la largeur de l'image originale.
    Si le ratio est trop faible, le crop est risqué pour la MRZ.
    """
    original_w = _safe_image_width(original_image)
    localized_w = _safe_image_width(localized_image)

    if not original_w or not localized_w:
        return None

    return localized_w / max(1, original_w)


def _should_use_full_image_for_passport_mrz(
    original_image,
    localized_image,
    min_width_ratio: float = 0.90,
) -> tuple[bool, float | None]:
    """
    Pour les passeports, un crop trop étroit peut couper la MRZ.
    Si le crop garde moins de 90% de la largeur originale, on utilise l'image complète.
    """
    ratio = _passport_crop_width_ratio(original_image, localized_image)

    if ratio is None:
        return False, None

    return ratio < min_width_ratio, ratio


def _should_use_full_image_for_roi_template(
    original_image,
    localized_image,
    min_width_ratio: float = 0.90,
) -> tuple[bool, float | None]:
    """
    Pour les templates ROI fixes, un crop trop étroit décale toutes les zones.
    Exemple SVK: si YOLO garde seulement ~72% de la largeur, les ROI tombent
    sur les mauvaises zones.

    Si le crop garde moins de 90% de la largeur originale, on ajoute l'image
    complète comme candidat prioritaire.
    """
    ratio = _passport_crop_width_ratio(original_image, localized_image)

    if ratio is None:
        return False, None

    return ratio < min_width_ratio, ratio


class GenericPipelineRunner:
    def __init__(self):
        self.templates = get_template_service()
        self.text_extractor = get_generic_extraction_service()
        self.roi_extractor = get_roi_template_extraction_service()
        self.passport_extractor = get_passport_extraction_service()
        self.registre_commerce_extractor = RegistreCommerceExtractor()
        self.invoice_extractor = InvoiceExtractor()
        self.normalizer = DocumentNormalizer()
        self.localizer = DocumentLocalizer()

    def _get(self, obj: Any, key: str, default=None):
        if obj is None:
            return default

        if isinstance(obj, dict):
            return obj.get(key, default)

        return getattr(obj, key, default)

    def _image_shape_info(self, image: Any) -> Dict[str, Any]:
        """
        Retourne des informations simples sur une image numpy/OpenCV.

        Utilisé seulement dans les diagnostics pour vérifier si l'OCR ciblée
        travaille sur l'image complète ou sur un crop / image prétraitée.
        """
        shape = getattr(image, "shape", None)

        if not shape or len(shape) < 2:
            return {
                "available": image is not None,
                "height": None,
                "width": None,
                "channels": None,
                "area": 0,
            }

        height = int(shape[0])
        width = int(shape[1])
        channels = int(shape[2]) if len(shape) > 2 else 1

        return {
            "available": True,
            "height": height,
            "width": width,
            "channels": channels,
            "area": height * width,
        }

    def _select_invoice_targeted_source_image(
        self,
        *,
        request: Any,
        image: Any,
        original_image: Any,
        fallback_image: Any,
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        Sélectionne l'image la plus fiable pour l'OCR ciblée facture.

        Priorité stricte :
        1. uploaded_file_image rechargée depuis request.metadata["file_path"] ;
        2. original_image ;
        3. input_image ;
        4. fallback_raw_ocr_image.

        Important :
        On NE choisit plus "la plus grande image" en premier, car une image
        prétraitée/cropée peut avoir une surface plus grande après resize/upscale.
        Pour récupérer Facture N° et Date, l'image uploadée brute est prioritaire.
        """
        loaded_from_file = None
        file_path = None
        file_load_error = None

        try:
            metadata = dict(getattr(request, "metadata", {}) or {})
            file_path = metadata.get("file_path")

            if file_path:
                loaded_from_file = load_image(file_path)
        except Exception as exc:
            file_load_error = f"{type(exc).__name__}: {exc}"

        ordered_candidates: List[Tuple[str, Any]] = [
            ("uploaded_file_image", loaded_from_file),
            ("original_image", original_image),
            ("input_image", image),
            ("fallback_raw_ocr_image", fallback_image),
        ]

        candidates_info: Dict[str, Dict[str, Any]] = {}

        for name, candidate in ordered_candidates:
            info = self._image_shape_info(candidate)
            candidates_info[name] = info

            if info.get("available") and int(info.get("area") or 0) > 0:
                return candidate, {
                    "selected": name,
                    "reason": "strict_priority_uploaded_file_first",
                    "fallback_used": name == "fallback_raw_ocr_image",
                    "file_path": file_path,
                    "file_load_error": file_load_error,
                    "selected_shape": info,
                    "candidates": candidates_info,
                }

        return fallback_image, {
            "selected": "fallback_raw_ocr_image",
            "reason": "no_valid_image_candidate",
            "fallback_used": True,
            "file_path": file_path,
            "file_load_error": file_load_error,
            "selected_shape": self._image_shape_info(fallback_image),
            "candidates": candidates_info,
        }

    def _safe_get_template(self, template_id: Optional[str]) -> Optional[Any]:
        if not template_id:
            return None

        try:
            template = self.templates.get(template_id)
            if template is not None:
                return template

        except Exception as exc:
            log.warning(
                "Template service lookup failed",
                extra={"template_id": template_id, "error": str(exc)},
            )

        try:
            from pathlib import Path
            import yaml

            path = Path("app/templates") / f"{template_id}.yaml"

            if not path.exists():
                log.warning(
                    "Template YAML not found",
                    extra={"template_id": template_id, "path": str(path)},
                )
                return None

            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if not isinstance(data, dict):
                log.warning(
                    "Template YAML is not a dict",
                    extra={"template_id": template_id, "path": str(path)},
                )
                return None

            data.setdefault("id", template_id)
            return data

        except Exception as exc:
            log.warning(
                "Direct YAML template load failed",
                extra={"template_id": template_id, "error": str(exc)},
            )
            return None

    def _template_id_from_document_type(self, document_type: str) -> Optional[str]:
        mapping = {
            "invoice": "invoice_tn",
            "passport": "passport_generic",
            "registre_commerce": "registre_commerce_tn",
            "business_registry": "registre_commerce_tn",
            "id_document": "id_document_generic",
        }

        return mapping.get(document_type)

    def _has_roi_fields(self, template: Any) -> bool:
        return bool(self._get(template, "roi_fields", []) or [])

    def _select_engine_name(
        self,
        request: ExtractionRequest,
        template: Optional[Any],
    ) -> str:
        requested = (request.engine or "auto").strip().lower()

        if requested and requested != "auto":
            return requested

        preferred = self._get(template, "preferred_engine")

        if preferred:
            return str(preferred).strip().lower()

        engines = self._get(template, "engines", {}) or {}

        if isinstance(engines, dict) and engines.get("primary"):
            return str(engines["primary"]).strip().lower()

        return "paddle"

    def _language_hints(
        self,
        request: ExtractionRequest,
        detected: Optional[str] = None,
        template: Optional[Any] = None,
    ) -> List[str]:
        request_hint = (request.language_hint or "").strip().lower()

        if request_hint and request_hint != "auto":
            return [request_hint]

        template_id = (
            self._get(template, "id")
            or request.template_id
            or ""
        )

        document_type = (
            self._get(template, "document_type")
            or request.document_type
            or ""
        )

        template_id = str(template_id or "")
        document_type = str(document_type or "")

        if template_id == "cin_tn" or document_type == "cin_tn":
            return ["ar", "fr", "en"]

        if template_id.startswith("midv_"):
            return ["en"]

        if document_type in {"passport", "id_document"}:
            return ["en"]

        if document_type in {"invoice", "registre_commerce"}:
            return ["fr", "en"]

        if detected and detected != "auto":
            return [detected]

        return ["en"]

    def _image_for_document_localization(
        self,
        prep: Dict[str, Any],
        request: ExtractionRequest,
        fallback_image,
    ):
        for key in ("original", "source_image", "raw_image"):
            img = prep.get(key)

            if img is not None and getattr(img, "size", 0) > 0:
                return img

        try:
            import cv2
            from pathlib import Path

            metadata = getattr(request, "metadata", {}) or {}
            file_path = metadata.get("file_path")

            if file_path and Path(file_path).exists():
                img = cv2.imread(str(file_path))

                if img is not None and img.size > 0:
                    return img

        except Exception:
            pass

        return fallback_image

    def _localize_document_safely(
        self,
        *,
        original_image,
        fallback_image,
        processing_mode: str,
    ) -> Tuple[Any, List[Dict[str, Any]], Dict[str, Any]]:
        try:
            localized = self.localizer.localize(
                original_image,
                mode=processing_mode,
            )

            return localized.image, localized.candidates, localized.diagnostics

        except Exception as exc:
            log.warning(
                "Document localization failed; falling back to OpenCV normalizer",
                extra={"error": str(exc)},
            )

            normalized = self.normalizer.normalize(
                fallback_image,
                mode=processing_mode,
                enable_rotation_candidates=True,
            )

            diagnostics = {
                "localizer": "document_localizer_failed_opencv_fallback",
                "method": "opencv_fallback_after_localizer_error",
                "error": f"{type(exc).__name__}: {exc}",
                "fallback_normalizer": normalized.diagnostics,
                "localized_shape": list(normalized.image.shape[:2]),
                "candidate_count": len(normalized.candidates),
            }

            return normalized.image, normalized.candidates, diagnostics

    def _resolve_template_before_ocr(
        self,
        request: ExtractionRequest,
        explicit_template: Optional[Any],
        classification_image=None,
    ) -> Tuple[Optional[Any], Dict[str, Any]]:
        routing: Dict[str, Any] = {
            "requested_document_type": request.document_type,
            "requested_template_id": request.template_id,
            "selected_template_id": None,
            "selected_document_type": None,
            "method": "none",
        }

        if explicit_template is not None:
            routing.update(
                {
                    "selected_template_id": self._get(explicit_template, "id"),
                    "selected_document_type": self._get(
                        explicit_template,
                        "document_type",
                        request.document_type,
                    ),
                    "method": "explicit_template_object",
                }
            )
            return explicit_template, routing

        if request.template_id:
            template = self._safe_get_template(request.template_id)

            if template is not None:
                routing.update(
                    {
                        "selected_template_id": request.template_id,
                        "selected_document_type": self._get(
                            template,
                            "document_type",
                            request.document_type,
                        ),
                        "method": "explicit_template_id",
                    }
                )
                return template, routing

        if request.document_type and request.document_type not in {"auto", "custom", "cin_tn"}:
            mapped_template_id = self._template_id_from_document_type(request.document_type)
            template = self._safe_get_template(mapped_template_id)

            if template is not None:
                routing.update(
                    {
                        "selected_template_id": mapped_template_id,
                        "selected_document_type": request.document_type,
                        "method": "document_type_mapping",
                    }
                )
                return template, routing

        image_path = (getattr(request, "metadata", {}) or {}).get("file_path")

        try:
            from app.classifiers.doc_family_classifier import classify_document

            cls = classify_document(
                image=classification_image,
                image_path=image_path,
            )

            template_id = cls.get("template_id")
            template = self._safe_get_template(template_id)

            if template is not None:
                routing.update(
                    {
                        "selected_template_id": template_id,
                        "selected_document_type": cls.get("document_type"),
                        "document_class": cls.get("document_class"),
                        "method": cls.get("method", "swin_image_classifier"),
                        "confidence": cls.get("confidence", 0.0),
                    }
                )
                return template, routing

            routing.update(
                {
                    "method": cls.get("method", "swin_image_classifier"),
                    "selected_template_id": template_id,
                    "selected_document_type": cls.get("document_type"),
                    "document_class": cls.get("document_class"),
                    "confidence": cls.get("confidence", 0.0),
                    "template_lookup_failed": bool(template_id),
                }
            )

        except Exception as exc:
            routing.update(
                {
                    "method": "classification_error",
                    "error": str(exc),
                }
            )

        return None, routing

    def _resolve_template_after_ocr(
        self,
        request: ExtractionRequest,
        raw_text: str,
        current_template: Optional[Any],
        routing: Dict[str, Any],
        classification_image=None,
    ) -> Tuple[Optional[Any], Dict[str, Any]]:
        if current_template is not None:
            return current_template, routing

        image_path = (getattr(request, "metadata", {}) or {}).get("file_path")

        try:
            from app.classifiers.doc_family_classifier import classify_document

            cls = classify_document(
                image=classification_image,
                image_path=image_path,
                text=raw_text,
            )

            template_id = cls.get("template_id")
            template = self._safe_get_template(template_id)

            if template is not None:
                routing.update(
                    {
                        "selected_template_id": template_id,
                        "selected_document_type": cls.get("document_type"),
                        "document_class": cls.get("document_class"),
                        "method": cls.get("method", "classifier"),
                        "confidence": cls.get("confidence", 0.0),
                    }
                )
                return template, routing

        except Exception as exc:
            log.warning(
                "Document classification failed",
                extra={"error": str(exc)},
            )

        routing["method"] = "generic_no_template"
        return None, routing

    def _is_passport_template_or_route(
        self,
        template: Optional[Any],
        routing: Dict[str, Any],
        request: ExtractionRequest,
    ) -> bool:
        template_id = (
            self._get(template, "id")
            or routing.get("selected_template_id")
            or request.template_id
            or ""
        )

        document_type = (
            self._get(template, "document_type")
            or routing.get("selected_document_type")
            or request.document_type
            or ""
        )

        document_class = str(routing.get("document_class") or "")

        haystack = " ".join(
            [
                str(template_id).lower(),
                str(document_type).lower(),
                document_class.lower(),
            ]
        )

        return "passport" in haystack

    def _is_invoice_template_or_route(
        self,
        template: Optional[Any],
        routing: Dict[str, Any],
        request: ExtractionRequest,
    ) -> bool:
        template_id = (
            self._get(template, "id")
            or routing.get("selected_template_id")
            or request.template_id
            or ""
        )

        document_type = (
            self._get(template, "document_type")
            or routing.get("selected_document_type")
            or request.document_type
            or ""
        )

        doc_family = (
            self._get(template, "doc_family")
            or routing.get("doc_family")
            or ""
        )

        haystack = " ".join(
            [
                str(template_id).lower(),
                str(document_type).lower(),
                str(doc_family).lower(),
            ]
        )

        return "invoice" in haystack or "facture" in haystack
    
    def _request_declares_invoice(
        self,
        request: ExtractionRequest,
        template: Optional[Any] = None,
        routing: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Strong guard for invoices.

        If the user explicitly sends document_type=invoice or template_id=invoice_tn,
        the pipeline must not execute ROI templates or passport/ID extraction.
        """
        routing = routing or {}

        values = [
            getattr(request, "document_type", None),
            getattr(request, "template_id", None),
            self._get(template, "id"),
            self._get(template, "document_type"),
            self._get(template, "doc_family"),
            routing.get("selected_template_id"),
            routing.get("selected_document_type"),
        ]

        haystack = " ".join(str(v or "").lower() for v in values)

        return (
            "invoice" in haystack
            or "facture" in haystack
            or "invoice_tn" in haystack
        )
    
    def _request_declares_registry(
        self,
        request: ExtractionRequest,
        template: Optional[Any] = None,
        routing: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Strong guard for Tunisian business registry / RNE documents.

        If the user explicitly sends document_type=registre_commerce,
        document_type=business_registry or template_id=registre_commerce_tn,
        the pipeline must not execute YOLO crop, ROI, passport or ID extraction.
        """
        routing = routing or {}

        values = [
            getattr(request, "document_type", None),
            getattr(request, "template_id", None),
            self._get(template, "id"),
            self._get(template, "document_type"),
            self._get(template, "doc_family"),
            routing.get("selected_template_id"),
            routing.get("selected_document_type"),
            routing.get("doc_family"),
        ]

        haystack = " ".join(str(v or "").lower() for v in values)

        return (
            "registre_commerce" in haystack
            or "registre-commerce" in haystack
            or "business_registry" in haystack
            or "registre_commerce_tn" in haystack
            or "rne" in haystack
        )

    def _force_registry_template_if_requested(
        self,
        request: ExtractionRequest,
        template: Optional[Any],
        routing: Dict[str, Any],
    ) -> Tuple[Optional[Any], Dict[str, Any]]:
        """
        If request clearly asks for registre de commerce / RNE, force
        registre_commerce_tn template and route to the dedicated full-page path.
        """
        if not self._request_declares_registry(request, template, routing):
            return template, routing

        current_template_id = str(
            self._get(template, "id")
            or routing.get("selected_template_id")
            or ""
        ).lower()

        if current_template_id in {"registre_commerce_tn", "registre_commerce"}:
            forced_template_id = current_template_id
        else:
            forced_template_id = getattr(request, "template_id", None) or "registre_commerce_tn"

        if "registre" not in str(forced_template_id).lower():
            forced_template_id = "registre_commerce_tn"

        forced_template = (
            self._safe_get_template(forced_template_id)
            or self._safe_get_template("registre_commerce_tn")
            or self._safe_get_template("registre_commerce")
            or template
        )

        routing.update(
            {
                "requested_document_type": getattr(request, "document_type", None),
                "requested_template_id": getattr(request, "template_id", None),
                "selected_template_id": self._get(forced_template, "id") or forced_template_id,
                "selected_document_type": "registre_commerce",
                "method": "forced_registry_request_guard",
                "forced_registry_guard": True,
            }
        )

        return forced_template, routing

    def _force_invoice_template_if_requested(
        self,
        request: ExtractionRequest,
        template: Optional[Any],
        routing: Dict[str, Any],
    ) -> Tuple[Optional[Any], Dict[str, Any]]:
        """
        If request clearly asks for invoice, force invoice_tn template.
        This prevents Swin/ROI from accidentally selecting an ID template.
        """
        if not self._request_declares_invoice(request, template, routing):
            return template, routing

        current_template_id = str(
            self._get(template, "id")
            or routing.get("selected_template_id")
            or ""
        ).lower()

        current_document_type = str(
            self._get(template, "document_type")
            or routing.get("selected_document_type")
            or ""
        ).lower()

        if current_template_id.startswith("invoice") or current_document_type == "invoice":
            if template is None:
                template_to_load = current_template_id or getattr(request, "template_id", None) or "invoice_tn"
                template = self._safe_get_template(template_to_load) or self._safe_get_template("invoice_tn")

            routing["selected_document_type"] = "invoice"
            routing["selected_template_id"] = (
                self._get(template, "id")
                or routing.get("selected_template_id")
                or getattr(request, "template_id", None)
                or "invoice_tn"
            )
            routing["method"] = routing.get("method") or "forced_invoice_request_guard"
            routing["forced_invoice_guard"] = True

            return template, routing

        forced_template_id = getattr(request, "template_id", None) or "invoice_tn"

        if "invoice" not in str(forced_template_id).lower():
            forced_template_id = "invoice_tn"

        forced_template = self._safe_get_template(forced_template_id) or self._safe_get_template("invoice_tn")

        routing.update(
            {
                "requested_document_type": getattr(request, "document_type", None),
                "requested_template_id": getattr(request, "template_id", None),
                "selected_template_id": self._get(forced_template, "id") or "invoice_tn",
                "selected_document_type": "invoice",
                "method": "forced_invoice_request_guard",
                "forced_invoice_guard": True,
                "previous_template_id": current_template_id or None,
                "previous_document_type": current_document_type or None,
            }
        )

        return forced_template, routing

    def _passport_core_valid_from_debug(
        self,
        debug: Optional[Dict[str, Any]],
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        True only when the selected MRZ is structurally valid and its core
        checksums are valid. This prevents partial/invalid MRZ text from being
        treated as a trustworthy extraction.
        """
        debug = debug or {}
        parsed = debug.get("selected_parsed") or debug.get("parsed") or {}

        if not isinstance(parsed, dict):
            parsed = {}

        checks = parsed.get("checks") or {}

        required_checks = (
            "document_number",
            "birth_date",
            "expiry_date",
            "composite",
        )

        if parsed.get("valid") is True and all(checks.get(k) is True for k in required_checks):
            return True

        # Fallback for service versions that do not expose selected_parsed but
        # correctly validate the mrz field.
        for field in fields or []:
            if field.get("name") == "mrz" and field.get("validated") is True:
                return True

        return False

    def _sanitize_passport_fields_for_invalid_mrz(
        self,
        fields: List[Dict[str, Any]],
        warnings: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
        """
        If the MRZ candidate is invalid, do not expose false-positive MRZ-derived
        values such as document_number/surname as validated fields.
        """
        passport_field_names = {
            "document_number",
            "surname",
            "given_names",
            "nationality",
            "birth_date",
            "gender",
            "expiry_date",
            "issuing_country",
            "personal_number",
            "mrz",
        }

        cleaned: List[Dict[str, Any]] = []
        cleaned_warnings = list(warnings or [])

        if "Passport MRZ core validation failed" not in cleaned_warnings:
            cleaned_warnings.append("Passport MRZ core validation failed")

        for field in fields:
            item = dict(field)
            name = str(item.get("name") or "")

            if name in passport_field_names:
                item["validated"] = False
                item["confidence"] = 0.0
                item["review_required"] = name in {
                    "document_number",
                    "surname",
                    "nationality",
                    "birth_date",
                    "expiry_date",
                }
                item["error"] = item.get("error") or "invalid_mrz"
                item["reasons"] = list(item.get("reasons") or []) + [
                    "suppressed:invalid_mrz_core_validation"
                ]

                # Keep the invalid MRZ string for diagnostics, but suppress all
                # derived field values from user-facing normalized_data.
                if name != "mrz":
                    item["value"] = None

            cleaned.append(self._dedupe_reasons(item))

        return cleaned, self._normalized_from_fields(cleaned), cleaned_warnings

    def _passport_field_score(self, fields: List[Dict[str, Any]]) -> float:
        weights = {
            "mrz": 12.0,
            "document_number": 10.0,
            "surname": 8.0,
            "given_names": 6.0,
            "nationality": 6.0,
            "birth_date": 7.0,
            "expiry_date": 7.0,
            "gender": 4.0,
            "issuing_country": 4.0,
            "personal_number": 2.0,
        }

        score = 0.0

        for field in fields:
            name = str(field.get("name") or "")
            value = field.get("value")
            valid = bool(field.get("validated"))
            conf = float(field.get("confidence", 0.0) or 0.0)

            if value in (None, "", []):
                continue

            weight = weights.get(name, 1.0)

            if valid:
                score += weight * (1.0 + conf)
            else:
                score += min(weight, 1.0) * 0.10

        required_missing = [
            f.get("name")
            for f in fields
            if f.get("review_required")
        ]

        score -= len(required_missing) * 3.0

        return round(score, 4)

    def _extract_passport_best_candidate(
        self,
        *,
        image,
        engine_name: str,
        language_hints: List[str],
        localized_candidates: Optional[List[Dict[str, Any]]] = None,
        stop_on_valid_mrz: bool = True,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        candidates = localized_candidates or [
            {
                "image": image,
                "angle": 0,
                "candidate_index": 0,
                "source": "localized_image",
            }
        ]

        candidate_debug: List[Dict[str, Any]] = []
        best: Optional[Dict[str, Any]] = None

        for candidate in candidates:
            candidate_image = candidate.get("image")
            angle = candidate.get("angle")
            candidate_index = candidate.get("candidate_index", 0)
            source = candidate.get("source")
            debug_prefix = f"passport_cand_{candidate_index}_angle_{angle}"

            if candidate_image is None or getattr(candidate_image, "size", 0) == 0:
                candidate_debug.append(
                    {
                        "angle": angle,
                        "source": source,
                        "candidate_index": candidate_index,
                        "debug_prefix": debug_prefix,
                        "score": -999.0,
                        "mrz_core_valid": False,
                        "warnings": ["empty passport candidate image"],
                    }
                )
                continue

            try:
                fields, normalized, debug, warnings = self.passport_extractor.extract(
                    image=candidate_image,
                    engine_name=engine_name,
                    language_hints=language_hints,
                    debug_prefix=debug_prefix,
                )

            except Exception as exc:
                candidate_debug.append(
                    {
                        "angle": angle,
                        "source": source,
                        "candidate_index": candidate_index,
                        "debug_prefix": debug_prefix,
                        "score": -999.0,
                        "mrz_core_valid": False,
                        "warnings": [
                            f"passport extractor failed: {type(exc).__name__}: {exc}"
                        ],
                    }
                )
                continue

            mrz_core_valid = self._passport_core_valid_from_debug(debug, fields)

            if not mrz_core_valid:
                fields, normalized, warnings = self._sanitize_passport_fields_for_invalid_mrz(
                    fields,
                    warnings,
                )

            score = self._passport_field_score(fields)

            if not mrz_core_valid:
                # Invalid MRZ must never beat a valid MRZ or look like a good candidate.
                score = min(score, -15.0)

            candidate_debug_item = {
                "angle": angle,
                "source": source,
                "candidate_index": candidate_index,
                "debug_prefix": debug_prefix,
                "score": score,
                "mrz_core_valid": mrz_core_valid,
                "validated_fields": sum(1 for f in fields if f.get("validated")),
                "present_fields": sum(
                    1 for f in fields if f.get("value") not in (None, "", [])
                ),
                "review_required_fields": [
                    f.get("name") for f in fields if f.get("review_required")
                ],
                "warnings": warnings,
                "passport_debug_summary": {
                    "selected_candidate": debug.get("selected_candidate"),
                    "selected_score": debug.get("selected_score"),
                },
            }

            candidate_debug.append(candidate_debug_item)

            item = {
                "score": score,
                "angle": angle,
                "source": source,
                "candidate_index": candidate_index,
                "debug_prefix": debug_prefix,
                "mrz_core_valid": mrz_core_valid,
                "fields": fields,
                "normalized": normalized,
                "debug": debug,
                "warnings": warnings,
            }

            if best is None or item["score"] > best["score"]:
                best = item

            if stop_on_valid_mrz and mrz_core_valid:
                merged_debug = {
                    "passport_extraction": "passport_mrz_first_best_candidate_v2_strict_early_stop",
                    "selected_angle": item["angle"],
                    "selected_source": item["source"],
                    "selected_candidate_index": item["candidate_index"],
                    "selected_debug_prefix": item["debug_prefix"],
                    "selected_score": item["score"],
                    "selected_mrz_core_valid": True,
                    "candidate_debug": candidate_debug,
                    "selected_candidate_debug": item["debug"],
                    "early_stop": {
                        "enabled": True,
                        "reason": "valid_mrz_core_checks",
                    },
                }
                return item["fields"], item["normalized"], merged_debug, item["warnings"]

        if best is None:
            return [], {}, {
                "passport_extraction": "no_candidate_extracted",
                "candidate_debug": candidate_debug,
            }, ["No passport candidate could be extracted"]

        merged_debug = {
            "passport_extraction": "passport_mrz_first_best_candidate_v2_strict",
            "selected_angle": best["angle"],
            "selected_source": best["source"],
            "selected_candidate_index": best["candidate_index"],
            "selected_debug_prefix": best["debug_prefix"],
            "selected_score": best["score"],
            "selected_mrz_core_valid": bool(best.get("mrz_core_valid")),
            "candidate_debug": candidate_debug,
            "selected_candidate_debug": best["debug"],
        }

        return best["fields"], best["normalized"], merged_debug, best["warnings"]

    def _suppress_weak_fields_without_strong_anchors(
        self,
        fields: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        strong_anchor_fields = {
            "number",
            "id_number",
            "birth_date",
            "expiry_date",
            "issue_date",
            "gender",
            "nationality",
        }

        weak_fields = {
            "name",
            "surname",
            "issue_place",
        }

        has_valid_strong_anchor = any(
            f.get("name") in strong_anchor_fields and f.get("validated")
            for f in fields
        )

        if has_valid_strong_anchor:
            return fields

        cleaned: List[Dict[str, Any]] = []

        for field in fields:
            item = dict(field)

            if item.get("name") in weak_fields and item.get("validated"):
                item["validated"] = False
                item["confidence"] = 0.0
                item["error"] = "weak_field_without_strong_anchor"
                item["review_required"] = bool(item.get("name") in {"name", "surname"})
                item["reasons"] = list(item.get("reasons") or []) + [
                    "suppressed:no_valid_strong_anchor"
                ]

            cleaned.append(item)

        return cleaned

    def _field_score(self, fields: List[Dict[str, Any]]) -> float:
        weights = {
            "number": 10.0,
            "id_number": 9.0,
            "birth_date": 7.0,
            "expiry_date": 7.0,
            "issue_date": 5.0,
            "gender": 5.0,
            "nationality": 5.0,
            "issue_place": 1.0,
            "name": 0.75,
            "surname": 0.75,
        }

        strong_anchor_fields = {
            "number",
            "id_number",
            "birth_date",
            "expiry_date",
            "issue_date",
            "gender",
            "nationality",
        }

        required_fields = {
            "birth_date",
            "expiry_date",
            "id_number",
            "name",
            "number",
            "surname",
        }

        score = 0.0
        valid_strong_anchors = 0
        valid_required = 0
        invalid_required = 0
        present_invalid = 0

        for field in fields:
            name = str(field.get("name") or "")
            value = field.get("value")
            valid = bool(field.get("validated"))
            review_required = bool(field.get("review_required"))
            conf = float(field.get("confidence", 0.0) or 0.0)

            has_value = value not in (None, "", [])
            weight = weights.get(name, 1.0)

            if valid:
                score += weight * (1.0 + conf)

                if name in strong_anchor_fields:
                    valid_strong_anchors += 1

                if name in required_fields:
                    valid_required += 1

            elif has_value:
                score += min(weight, 1.0) * 0.20
                present_invalid += 1

            if review_required:
                invalid_required += 1
                score -= max(2.0, weight * 0.70)

        if valid_strong_anchors == 0:
            score -= 20.0

        if valid_strong_anchors == 0 and valid_required <= 1:
            score -= 10.0

        if valid_strong_anchors >= 2:
            score += 8.0

        if valid_strong_anchors >= 3:
            score += 8.0

        if invalid_required >= 4:
            score -= 8.0

        if present_invalid >= 4:
            score -= 4.0

        return round(score, 4)

    def _call_roi_extractor_compatible(
        self,
        *,
        candidate_image,
        template,
        engine_name: str,
        language_hints: List[str],
        debug_prefix: str,
        content_bbox_norm: Optional[List[float]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        try:
            return self.roi_extractor.extract(
                image=candidate_image,
                template=template,
                engine_name=engine_name,
                language_hints=language_hints,
                debug_prefix=debug_prefix,
                content_bbox_norm=content_bbox_norm,
            )

        except TypeError as exc:
            if "content_bbox_norm" not in str(exc):
                raise

            return self.roi_extractor.extract(
                image=candidate_image,
                template=template,
                engine_name=engine_name,
                language_hints=language_hints,
                debug_prefix=debug_prefix,
            )

    def _extract_roi_best_candidate(
        self,
        *,
        image,
        template,
        engine_name: str,
        language_hints: List[str],
        processing_mode: str,
        localized_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        if localized_candidates:
            candidates = localized_candidates
            normalizer_diagnostics = {
                "normalizer": "skipped",
                "reason": "using_document_localizer_candidates",
                "candidate_count": len(candidates),
            }

        else:
            normalized = self.normalizer.normalize(
                image,
                mode=processing_mode,
                enable_rotation_candidates=True,
            )

            candidates = normalized.candidates
            normalizer_diagnostics = normalized.diagnostics

        candidate_debug: List[Dict[str, Any]] = []
        best: Optional[Dict[str, Any]] = None

        for candidate in candidates:
            angle = candidate.get("angle")
            candidate_image = candidate.get("image")
            candidate_index = candidate.get("candidate_index", 0)
            rotation_index = candidate.get("rotation_index")
            source = candidate.get("source")
            content_bbox_norm = candidate.get("content_bbox_norm")

            debug_prefix = f"cand_{candidate_index}_angle_{angle}"

            if candidate_image is None or getattr(candidate_image, "size", 0) == 0:
                candidate_debug.append(
                    {
                        "angle": angle,
                        "source": source,
                        "candidate_index": candidate_index,
                        "rotation_index": rotation_index,
                        "debug_prefix": debug_prefix,
                        "content_bbox_norm": content_bbox_norm,
                        "score": -999.0,
                        "validated_fields": 0,
                        "validated_strong_fields": 0,
                        "present_fields": 0,
                        "review_required_fields": [],
                        "warnings": ["empty candidate image"],
                    }
                )
                continue

            try:
                fields, normalized_data, debug, warnings = self._call_roi_extractor_compatible(
                    candidate_image=candidate_image,
                    template=template,
                    engine_name=engine_name,
                    language_hints=language_hints,
                    debug_prefix=debug_prefix,
                    content_bbox_norm=content_bbox_norm,
                )

                fields = self._suppress_weak_fields_without_strong_anchors(fields)
                normalized_data = self._normalized_from_fields(fields)

            except Exception as exc:
                candidate_debug.append(
                    {
                        "angle": angle,
                        "source": source,
                        "candidate_index": candidate_index,
                        "rotation_index": rotation_index,
                        "debug_prefix": debug_prefix,
                        "content_bbox_norm": content_bbox_norm,
                        "score": -999.0,
                        "validated_fields": 0,
                        "validated_strong_fields": 0,
                        "present_fields": 0,
                        "review_required_fields": [],
                        "warnings": [
                            f"ROI candidate extraction failed: {type(exc).__name__}: {exc}"
                        ],
                    }
                )
                continue

            score = self._field_score(fields)

            strong_names = {
                "number",
                "id_number",
                "birth_date",
                "expiry_date",
                "issue_date",
                "gender",
                "nationality",
            }

            candidate_debug.append(
                {
                    "angle": angle,
                    "source": source,
                    "candidate_index": candidate_index,
                    "rotation_index": rotation_index,
                    "debug_prefix": debug_prefix,
                    "content_bbox_norm": content_bbox_norm,
                    "score": score,
                    "validated_fields": sum(1 for f in fields if f.get("validated")),
                    "validated_strong_fields": sum(
                        1
                        for f in fields
                        if f.get("validated") and f.get("name") in strong_names
                    ),
                    "present_fields": sum(
                        1 for f in fields if f.get("value") not in (None, "", [])
                    ),
                    "review_required_fields": [
                        f.get("name") for f in fields if f.get("review_required")
                    ],
                    "warnings": warnings,
                }
            )

            item = {
                "score": score,
                "angle": angle,
                "source": source,
                "candidate_index": candidate_index,
                "rotation_index": rotation_index,
                "debug_prefix": debug_prefix,
                "content_bbox_norm": content_bbox_norm,
                "fields": fields,
                "normalized_data": normalized_data,
                "debug": debug,
                "warnings": warnings,
            }

            if best is None or item["score"] > best["score"]:
                best = item

            strong_valid_count = sum(
                1
                for f in fields
                if f.get("validated") and f.get("name") in strong_names
            )
            review_required_count = sum(1 for f in fields if f.get("review_required"))

            if (
                str(source) == "full_image_roi_width_guard"
                and score >= 80.0
                and strong_valid_count >= 5
                and review_required_count == 0
            ):
                merged_debug = {
                    "roi_extraction": "template_roi_with_document_localization_v3_weighted_score_safe",
                    "selected_angle": item["angle"],
                    "selected_source": item.get("source"),
                    "selected_candidate_index": item.get("candidate_index"),
                    "selected_rotation_index": item.get("rotation_index"),
                    "selected_debug_prefix": item.get("debug_prefix"),
                    "selected_content_bbox_norm": item.get("content_bbox_norm"),
                    "selected_score": item["score"],
                    "document_normalizer": normalizer_diagnostics,
                    "candidate_debug": candidate_debug,
                    "selected_candidate_debug": item["debug"],
                    "early_stop": {
                        "enabled": True,
                        "reason": "strong_full_image_roi_width_guard_candidate",
                        "min_score": 80.0,
                        "validated_strong_fields": strong_valid_count,
                    },
                }

                return item["fields"], item["normalized_data"], merged_debug, item["warnings"]

        if best is None:
            return [], {}, {
                "roi_extraction": "no_candidate_extracted",
                "document_normalizer": normalizer_diagnostics,
                "candidate_debug": candidate_debug,
            }, ["No ROI candidate could be extracted"]

        merged_debug = {
            "roi_extraction": "template_roi_with_document_localization_v3_weighted_score_safe",
            "selected_angle": best["angle"],
            "selected_source": best.get("source"),
            "selected_candidate_index": best.get("candidate_index"),
            "selected_rotation_index": best.get("rotation_index"),
            "selected_debug_prefix": best.get("debug_prefix"),
            "selected_content_bbox_norm": best.get("content_bbox_norm"),
            "selected_score": best["score"],
            "document_normalizer": normalizer_diagnostics,
            "candidate_debug": candidate_debug,
            "selected_candidate_debug": best["debug"],
        }

        return best["fields"], best["normalized_data"], merged_debug, best["warnings"]

    def _merge_field_results(
        self,
        first: List[Dict[str, Any]],
        second: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_name: Dict[str, Dict[str, Any]] = {}

        for field in first + second:
            name = field.get("name")

            if not name:
                continue

            old = by_name.get(name)

            if old is None:
                by_name[name] = field
                continue

            old_valid = bool(old.get("validated"))
            new_valid = bool(field.get("validated"))

            old_has_value = old.get("value") not in (None, "", [])
            new_has_value = field.get("value") not in (None, "", [])

            old_score = float(old.get("confidence", 0.0) or 0.0)
            new_score = float(field.get("confidence", 0.0) or 0.0)

            if old_valid and not new_valid:
                continue

            if new_valid and not old_valid:
                by_name[name] = field
                continue

            if new_has_value and not old_has_value:
                by_name[name] = field
                continue

            if old_has_value and not new_has_value:
                continue

            if new_score > old_score:
                by_name[name] = field

        return list(by_name.values())


    def _is_registry_legacy_result(self, fields: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Détecte un résultat de registre legacy à partir des champs déjà extraits.

        Le legacy contient souvent :
        - une raison sociale latine avec CORPORATION / COMPANY / SARL ;
        - des numéros administratifs D..., B..., G...
        """
        values = " ".join(
            str(f.get("value") or "")
            for f in (fields or [])
        ).upper()

        return bool(
            "CORPORATION" in values
            or "COMPANY" in values
            or re.search(r"\b[DBG][0-9]{6,12}\b", values)
        )

    def _registry_critical_field_names(
        self,
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> set[str]:
        """
        Champs critiques par variante.

        RNE moderne : 6 champs stables.
        Registre legacy : seulement 3 champs critiques, car capital/dates modernes
        ne sont pas toujours présents ou lisibles dans les anciens scans.
        """
        if fields and self._is_registry_legacy_result(fields):
            return {
                "date_extrait",
                "identifiant_unique",
                "raison_sociale",
            }

        return {
            "identifiant_unique",
            "raison_sociale",
            "date_extrait",
            "capital",
            "date_publication",
            "date_debut_activite",
        }

    def _field_has_valid_value(self, field: Dict[str, Any]) -> bool:
        return bool(field.get("validated")) and field.get("value") not in (None, "", [])

    def _registry_missing_critical_fields(self, fields: List[Dict[str, Any]]) -> List[str]:
        critical = self._registry_critical_field_names(fields)
        present = {
            str(f.get("name"))
            for f in fields
            if f.get("name") in critical
            and self._field_has_valid_value(f)
        }
        return sorted(critical - present)

    def _template_output_key(self, template: Optional[Any], field_name: str) -> str:
        mapping = self._get(template, "output_mapping", {}) or {}

        if isinstance(mapping, dict) and mapping.get(field_name):
            return str(mapping[field_name])

        registry_fallback_mapping = {
            "date_extrait": "extractionDate",
            "identifiant_unique": "uniqueIdentifier",
            "raison_sociale": "companyName",
            "numero_registre": "registrationNumber",
            "numero_depot": "depositNumber",
            "numero_interne": "internalNumber",
            "capital": "capital",
            "date_publication": "publicationDate",
            "date_debut_activite": "activityStartDate",
            "dirigeant_date_naissance": "directorBirthDate",
        }
        if field_name in registry_fallback_mapping:
            return registry_fallback_mapping[field_name]

        fields = self._get(template, "fields", []) or []

        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                if field.get("name") == field_name and field.get("output_key"):
                    return str(field["output_key"])

        return field_name

    def _normalized_from_fields_with_template(
        self,
        fields: List[Dict[str, Any]],
        template: Optional[Any],
    ) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}

        for field in fields:
            name = field.get("name")
            value = field.get("value")

            if not name:
                continue

            if value in (None, "", []):
                continue

            if not field.get("validated"):
                continue

            normalized[self._template_output_key(template, str(name))] = value

        return normalized

    def _normalize_registry_compare_value(self, name: str, value: Any) -> str:
        if value is None:
            return ""

        text = str(value).strip()

        if name in {"identifiant_unique", "capital"}:
            return re.sub(r"\s+", "", text).upper()

        if name in {"date_extrait", "date_publication", "date_debut_activite"}:
            return text.replace("-", "/")

        return re.sub(r"\s+", " ", text).strip().upper()

    def _annotate_registry_fields(
        self,
        fields: List[Dict[str, Any]],
        *,
        engine_name: str,
        source: str,
    ) -> List[Dict[str, Any]]:
        annotated: List[Dict[str, Any]] = []

        for field in fields:
            item = dict(field)
            item["selected_engine"] = item.get("selected_engine") or engine_name
            item["selected_source"] = item.get("selected_source") or source
            item["reasons"] = list(item.get("reasons") or []) + [f"ocr_engine:{engine_name}"]
            annotated.append(self._dedupe_reasons(item))

        return annotated

    def _merge_registry_ocr_fields(
        self,
        primary_fields: List[Dict[str, Any]],
        secondary_fields: List[Dict[str, Any]],
        *,
        primary_engine: str,
        secondary_engine: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Field-level merge for Tunisian RNE extraction.

        Policy:
        - merge only critical RNE fields;
        - never concatenate raw OCR texts;
        - keep a valid primary value unless secondary fills a missing field or
          is clearly stronger;
        - when both engines return the same normalized value, mark consensus and
          boost confidence slightly.
        """
        critical = self._registry_critical_field_names(primary_fields)
        by_name: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        replaced: List[str] = []
        filled: List[str] = []
        consensus: List[str] = []
        ignored_secondary: List[str] = []

        for field in primary_fields:
            name = field.get("name")
            if not name:
                continue
            item = dict(field)
            item["selected_engine"] = item.get("selected_engine") or primary_engine
            item["selected_source"] = item.get("selected_source") or "primary_registry_ocr"
            by_name[str(name)] = item
            order.append(str(name))

        for field in secondary_fields:
            name = str(field.get("name") or "")

            if name not in critical:
                continue

            secondary_value = field.get("value")
            secondary_valid = bool(field.get("validated"))

            if not secondary_valid or secondary_value in (None, "", []):
                continue

            secondary_conf = float(field.get("confidence", 0.0) or 0.0)
            old = by_name.get(name)

            candidate = dict(field)
            candidate["selected_engine"] = secondary_engine
            candidate["selected_source"] = "secondary_registry_ocr"
            candidate["reasons"] = list(candidate.get("reasons") or []) + [
                f"selected_from:{secondary_engine}_registry_ocr"
            ]

            if old is None:
                by_name[name] = self._dedupe_reasons(candidate)
                order.append(name)
                filled.append(name)
                continue

            old_value = old.get("value")
            old_valid = bool(old.get("validated"))
            old_conf = float(old.get("confidence", 0.0) or 0.0)

            if not old_valid or old_value in (None, "", []):
                candidate["reasons"] = list(candidate.get("reasons") or []) + [
                    "filled_missing_primary_field"
                ]
                by_name[name] = self._dedupe_reasons(candidate)
                filled.append(name)
                continue

            old_cmp = self._normalize_registry_compare_value(name, old_value)
            new_cmp = self._normalize_registry_compare_value(name, secondary_value)

            if old_cmp and new_cmp and old_cmp == new_cmp:
                merged = dict(old)
                merged["selected_engine"] = f"{primary_engine}+{secondary_engine}"
                merged["selected_source"] = "registry_ocr_consensus"
                merged["confidence"] = min(0.99, max(old_conf, secondary_conf) + 0.05)
                merged["reasons"] = list(merged.get("reasons") or []) + [
                    f"confirmed_by:{secondary_engine}_registry_ocr"
                ]
                by_name[name] = self._dedupe_reasons(merged)
                consensus.append(name)
                continue

            replace = False

            if secondary_conf > old_conf + 0.15:
                replace = True

            if replace:
                candidate["reasons"] = list(candidate.get("reasons") or []) + [
                    f"replaced_{primary_engine}_field",
                    f"previous_value:{old_value}",
                ]
                by_name[name] = self._dedupe_reasons(candidate)
                replaced.append(name)
            else:
                ignored_secondary.append(name)

        merge_debug = {
            "policy": "critical_fields_only_field_level_merge_no_raw_text_concat",
            "primary_engine": primary_engine,
            "secondary_engine": secondary_engine,
            "critical_fields": sorted(critical),
            "filled_from_secondary": sorted(set(filled)),
            "replaced_by_secondary": sorted(set(replaced)),
            "confirmed_by_consensus": sorted(set(consensus)),
            "ignored_secondary": sorted(set(ignored_secondary)),
        }

        return [by_name[name] for name in order if name in by_name], merge_debug

    def _normalized_from_fields(
        self,
        fields: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}

        for field in fields:
            name = field.get("name")
            value = field.get("value")

            if not name:
                continue

            if value in (None, "", []):
                continue

            if not field.get("validated"):
                continue

            normalized[name] = value

        return normalized

    def _dedupe_reasons(self, item: Dict[str, Any]) -> Dict[str, Any]:
        reasons = item.get("reasons") or []
        if not isinstance(reasons, list):
            return item

        seen = set()
        cleaned = []

        for reason in reasons:
            if reason in seen:
                continue
            seen.add(reason)
            cleaned.append(reason)

        item["reasons"] = cleaned
        return item
    
    def _registry_field_outputs_to_dicts(
    self,
    fields: List[Any],
    *,
    engine_name: str,
    source: str,
) -> List[Dict[str, Any]]:
        """
        Convertit les FieldOutput du RegistreCommerceExtractor en dicts API.

        Utilisé pour la fusion PaddleOCR / EasyOCR champ par champ.
        """
        out: List[Dict[str, Any]] = []

        for f in fields or []:
            if isinstance(f, dict):
                item = dict(f)
            else:
                name = getattr(f, "name", None)
                value = getattr(f, "value", None)
                confidence = float(getattr(f, "confidence", 0.0) or 0.0)
                validated = bool(getattr(f, "validated", False))
                raw_value = getattr(f, "raw_text", None)
                error = getattr(f, "error", None)

                item = {
                    "name": name,
                    "value": value,
                    "confidence": round(confidence, 3),
                    "validated": validated,
                    "raw_text": raw_value,
                    "raw_template_field": name,
                    "error": error,
                    "selected_engine": engine_name,
                    "selected_source": source,
                    "review_required": False,
                    "reasons": (
                        [f"selected_from:{source}", f"ocr_engine:{engine_name}"]
                        if value not in (None, "", [])
                        else ["field unresolved", f"ocr_engine:{engine_name}"]
                    ),
                }

            if item.get("name"):
                item["selected_engine"] = item.get("selected_engine") or engine_name
                item["selected_source"] = item.get("selected_source") or source
                item["reasons"] = list(item.get("reasons") or []) + [
                    f"ocr_engine:{engine_name}"
                ]
                out.append(self._dedupe_reasons(item))

        return out

    def _filter_non_text_visual_fields(
        self,
        fields: List[Dict[str, Any]],
        normalized_data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Remove visual-only placeholders from the API response.

        For the SVK ID demo, face/photo/signature are not extracted as image
        payloads. Keeping them as invalid textual fields reduces coverage and
        makes the result look worse although the textual extraction is correct.
        """
        visual_names = {"face", "photo", "signature"}

        cleaned_fields = [
            field
            for field in fields
            if str(field.get("name") or "").lower() not in visual_names
        ]

        cleaned_normalized = dict(normalized_data or {})

        for key in list(cleaned_normalized.keys()):
            if str(key).lower() in visual_names:
                cleaned_normalized.pop(key, None)

        return cleaned_fields, cleaned_normalized

    def _clean_svk_gender(self, raw_text: Any) -> Optional[str]:
        """Extract gender from noisy SVK ROI text, e.g. 'Metno M' -> 'M'."""
        if raw_text is None:
            return None

        text = str(raw_text).upper()
        matches = re.findall(r"(?:^|[^A-Z])([MF])(?:[^A-Z]|$)", text)

        if matches:
            return matches[-1]

        return None

    def _clean_svk_issue_place(self, raw_text: Any) -> Optional[str]:
        """Extract issue place from noisy OCR, e.g. noisy text -> 'Poprad'."""
        if raw_text is None:
            return None

        text = str(raw_text)
        words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", text)

        blacklist = {
            "wyoa",
            "vydal",
            "issued",
            "lssued",
            "issuer",
            "by",
            "dy",
            "di",
            "ri",
            "izilen",
            "svk",
            "id",
            "card",
            "preukaz",
        }

        candidates: List[str] = []

        for word in words:
            low = word.lower()

            if low in blacklist:
                continue

            if len(word) < 3:
                continue

            candidates.append(word)

        if not candidates:
            return None

        value = candidates[0]
        return value[:1].upper() + value[1:].lower()

    def _clean_svk_person_name(self, raw_text: Any) -> Optional[str]:
        """Clean noisy SVK name/surname OCR, e.g. 'Reclnsua DGDI Zugan' -> 'Zugan'."""
        if raw_text is None:
            return None

        text = str(raw_text)
        words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", text)

        blacklist = {
            "prezvisko",
            "surname",
            "meno",
            "given",
            "names",
            "name",
            "reclnsua",
            "dgdi",
            "dgd",
            "id",
            "card",
            "slovenska",
            "republika",
            "obciansky",
            "preukaz",
        }

        candidates: List[str] = []

        for word in words:
            low = word.lower()

            if low in blacklist:
                continue

            if len(word) < 3:
                continue

            candidates.append(word)

        if not candidates:
            return None

        value = candidates[-1]
        return value[:1].upper() + value[1:].lower()

    def _postprocess_midv_svk_id_fields(
        self,
        fields: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Conservative post-processing for MIDV SVK ID only.

        This keeps the change isolated to the SVK template and avoids affecting
        passport, invoice, CIN or custom-template behavior.
        """
        cleaned: List[Dict[str, Any]] = []

        for field in fields:
            item = dict(field)
            name = str(item.get("name") or "")
            raw_text = item.get("raw_text")

            if name == "gender" and not item.get("validated"):
                gender = self._clean_svk_gender(raw_text)

                if gender:
                    item["value"] = gender
                    item["validated"] = True
                    item["confidence"] = max(float(item.get("confidence") or 0.0), 0.70)
                    item["error"] = None
                    item["review_required"] = False
                    item["reasons"] = list(item.get("reasons") or []) + [
                        "postprocessed:midv_svk_gender"
                    ]

            elif name == "issue_place":
                place = self._clean_svk_issue_place(raw_text)

                if place:
                    item["value"] = place
                    item["validated"] = True
                    item["confidence"] = max(float(item.get("confidence") or 0.0), 0.70)
                    item["error"] = None
                    item["review_required"] = False
                    item["reasons"] = list(item.get("reasons") or []) + [
                        "postprocessed:midv_svk_issue_place"
                    ]

            elif name in {"surname", "name"}:
                clean_name = self._clean_svk_person_name(raw_text)

                if clean_name:
                    item["value"] = clean_name
                    item["validated"] = True
                    item["confidence"] = max(float(item.get("confidence") or 0.0), 0.70)
                    item["error"] = None
                    item["review_required"] = False
                    item["reasons"] = list(item.get("reasons") or []) + [
                        f"postprocessed:midv_svk_{name}"
                    ]

            cleaned.append(self._dedupe_reasons(item))

        return cleaned

    def _roi_result_is_strong_enough(
        self,
        fields: List[Dict[str, Any]],
        roi_debug: Dict[str, Any],
        min_score: float = 80.0,
    ) -> bool:
        if not fields:
            return False

        total = len(fields)
        valid_count = sum(1 for f in fields if f.get("validated"))
        required_missing = [f.get("name") for f in fields if f.get("review_required")]
        coverage = valid_count / total if total else 0.0
        score = float(roi_debug.get("selected_score", 0.0) or 0.0)

        return not required_missing and coverage >= 0.90 and score >= min_score

    def _field_output_get(self, field: Any, key: str, default=None):
        if isinstance(field, dict):
            return field.get(key, default)

        return getattr(field, key, default)

    def _invoice_output_key(self, name: str) -> str:
        """
        Map internal invoice field names to API normalized_data keys.

        Keep internal field names stable in English, while exposing camelCase keys
        that are easier to consume in the UI/API response. This mapping matches
        invoice_tn.yaml v3.0 and InvoiceExtractor(invoice_tn_multi_profile).
        """
        mapping = {
            "invoice_profile": "invoiceProfile",
            "invoice_number": "invoiceNumber",
            "invoice_date": "invoiceDate",
            "reference_unique": "referenceUnique",
            "supplier_name": "supplierName",
            "customer_name": "customerName",
            "supplier_tax_id": "supplierTaxId",
            "customer_tax_id": "customerTaxId",
            # Backward compatibility with older invoice_extractor versions.
            "tax_id": "taxId",
            "period_start": "periodStart",
            "period_end": "periodEnd",
            "payment_due_date": "paymentDueDate",
            "total_ht": "totalHT",
            "vat_rate": "vatRate",
            "vat_amount": "vatAmount",
            "stamp_amount": "stampAmount",
            "total_ttc": "totalTTC",
            "currency": "currency",
            "line_items": "lineItems",
            "amount_consistency": "amountConsistency",
        }

        return mapping.get(name, name)

    def _invoice_label_fr(self, name: str) -> str:
        labels = {
            "invoice_profile": "Profil de facture",
            "invoice_number": "Numéro de facture",
            "invoice_date": "Date de facture",
            "reference_unique": "Référence unique",
            "supplier_name": "Fournisseur",
            "customer_name": "Client",
            "supplier_tax_id": "Matricule fiscal fournisseur",
            "customer_tax_id": "Matricule fiscal client",
            "tax_id": "Matricule fiscal",
            "period_start": "Début de période",
            "period_end": "Fin de période",
            "payment_due_date": "Date limite de paiement",
            "total_ht": "Total H.T.",
            "vat_rate": "Taux TVA",
            "vat_amount": "Montant TVA",
            "stamp_amount": "Droit de timbre",
            "total_ttc": "Montant T.T.C.",
            "currency": "Devise",
            "line_items": "Contenu du tableau",
            "amount_consistency": "Cohérence des montants",
        }
        return labels.get(name, name)

    def _invoice_missing_header_fields(self, fields: List[Dict[str, Any]]) -> List[str]:
        by_name = {f.get("name"): f for f in fields}
        missing: List[str] = []

        for name in ("invoice_number", "invoice_date"):
            field = by_name.get(name)
            if not field or not field.get("validated") or field.get("value") in (None, "", []):
                missing.append(name)

        return missing

    def _invoice_normalized_from_invoice_fields(self, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}

        for field in fields:
            name = field.get("name")
            value = field.get("value")

            if not name or value in (None, "", []):
                continue

            if not field.get("validated"):
                continue

            normalized[self._invoice_output_key(str(name))] = value

        return normalized

    def _invoice_warnings_from_fields(self, fields: List[Dict[str, Any]]) -> List[str]:
        warnings: List[str] = []
        critical_fields = {"invoice_number", "invoice_date", "total_ttc"}

        for field in fields:
            name = field.get("name")

            if name in critical_fields and not field.get("validated"):
                warnings.append(f"Required invoice field '{name}' missing or invalid")

            if name == "amount_consistency" and field.get("value") is False:
                warnings.append("Invoice amount consistency check failed")

        cleaned: List[str] = []
        seen = set()

        for warning in warnings:
            if warning in seen:
                continue
            seen.add(warning)
            cleaned.append(warning)

        return cleaned

    def _merge_invoice_targeted_header_fields(
        self,
        base_fields: List[Dict[str, Any]],
        targeted_fields: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge policy:
        - targeted OCR may fill invoice_number and invoice_date only;
        - targeted OCR must never overwrite amounts, TVA, TTC, timbre or consistency.
        """
        allowed = {"invoice_number", "invoice_date"}
        by_name: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for field in base_fields:
            name = field.get("name")
            if not name:
                continue
            by_name[str(name)] = dict(field)
            order.append(str(name))

        for field in targeted_fields:
            name = field.get("name")

            if name not in allowed:
                continue

            if not field.get("validated") or field.get("value") in (None, "", []):
                continue

            old = by_name.get(str(name))

            if old and old.get("validated") and old.get("value") not in (None, "", []):
                continue

            merged = dict(field)
            merged["selected_source"] = "targeted_header_ocr"
            merged["reasons"] = list(merged.get("reasons") or []) + [
                "filled_from:invoice_targeted_header_ocr"
            ]

            by_name[str(name)] = merged

            if str(name) not in order:
                order.append(str(name))

        return [by_name[name] for name in order if name in by_name]

    def _extract_invoice_from_raw_text(
        self,
        raw_text: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        raw_fields = self.invoice_extractor.extract(
            raw_text,
            metadata={"source": "generic_runner"},
        )

        critical_fields = {
            "invoice_number",
            "invoice_date",
            "total_ttc",
        }

        fields: List[Dict[str, Any]] = []
        normalized: Dict[str, Any] = {}
        warnings: List[str] = []
        debug_fields: List[Dict[str, Any]] = []

        for raw_field in raw_fields:
            name = self._field_output_get(raw_field, "name")
            value = self._field_output_get(raw_field, "value")
            confidence = float(self._field_output_get(raw_field, "confidence", 0.0) or 0.0)
            validated = bool(self._field_output_get(raw_field, "validated", False))
            raw_field_text = self._field_output_get(raw_field, "raw_text")
            error = self._field_output_get(raw_field, "error")

            if not name:
                continue

            is_critical = name in critical_fields

            if not is_critical and value in (None, "", []) and not validated:
                debug_fields.append(
                    {
                        "name": name,
                        "label": self._invoice_label_fr(str(name)),
                        "value": value,
                        "validated": validated,
                        "error": error,
                        "skipped_from_response": True,
                    }
                )
                continue

            if name == "amount_consistency" and value is None:
                debug_fields.append(
                    {
                        "name": name,
                        "label": self._invoice_label_fr(str(name)),
                        "value": value,
                        "validated": validated,
                        "error": error,
                        "skipped_from_response": True,
                    }
                )
                continue

            review_required = bool(is_critical and not validated)

            if review_required:
                warnings.append(f"Required invoice field '{name}' missing or invalid")

            if name == "amount_consistency" and value is False:
                review_required = True
                warnings.append("Invoice amount consistency check failed")

            field_result = {
                "name": name,
                "label": self._invoice_label_fr(str(name)),
                "value": value,
                "confidence": round(confidence, 3),
                "validated": validated,
                "raw_text": raw_field_text,
                "raw_template_field": name,
                "error": error,
                "selected_engine": "invoice_extractor",
                "selected_source": "raw_text_invoice_rules",
                "review_required": review_required,
                "reasons": (
                    ["selected_from:invoice_extractor"]
                    if value not in (None, "", [])
                    else ["field unresolved"]
                ),
            }

            fields.append(field_result)

            if validated and value not in (None, "", []):
                normalized[self._invoice_output_key(name)] = value

            debug_fields.append(
                {
                    "name": name,
                    "value": value,
                    "validated": validated,
                    "error": error,
                    "confidence": round(confidence, 3),
                    "included_in_response": True,
                }
            )

        debug = {
            "invoice_extraction": "invoice_extractor_v3_tn_multi_profile_raw_text_rules",
            "critical_fields": sorted(critical_fields),
            "fields": debug_fields,
        }

        return fields, normalized, debug, warnings

    def _build_response(
        self,
        *,
        job_id: str,
        request: ExtractionRequest,
        template: Optional[Any],
        routing: Dict[str, Any],
        raw_text: str,
        fields: List[Dict[str, Any]],
        normalized_data: Dict[str, Any],
        language_detected: str,
        engine_name: str,
        quality: Dict[str, Any],
        transforms: Dict[str, Any],
        layout: Dict[str, Any],
        diagnostics_extra: Dict[str, Any],
        warnings: List[str],
        started: float,
    ) -> ExtractionResponse:
        total = len(fields)
        valid_count = sum(1 for f in fields if f.get("validated"))
        present_count = sum(1 for f in fields if f.get("value") not in (None, "", []))
        required_missing = [f.get("name") for f in fields if f.get("review_required")]

        confidences = [
            float(f.get("confidence", 0.0) or 0.0)
            for f in fields
        ]

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        coverage = valid_count / total if total else 0.0

        if total == 0:
            status = "failed"
        elif required_missing:
            status = "review_required"
        elif coverage >= 0.70:
            status = "success"
        else:
            status = "partial"

        global_conf = round((0.65 * avg_conf) + (0.35 * coverage), 4)

        template_id = (
            self._get(template, "id")
            or request.template_id
            or routing.get("selected_template_id")
        )

        document_type = (
            self._get(template, "document_type")
            or routing.get("selected_document_type")
            or request.document_type
            or "unknown"
        )

        business_validation = {
            "status": status,
            "review_required": bool(required_missing),
            "review_reasons": [
                f"Missing or invalid required field: {name}"
                for name in required_missing
            ],
            "valid_fields_ok": valid_count,
            "present_fields": present_count,
            "field_coverage": round(coverage, 4),
            "avg_field_confidence": round(avg_conf, 4),
            "business_confidence": global_conf,
            "quality_score": (quality or {}).get("quality_score"),
        }

        diagnostics = {
            "mode": "generic_template_v5_detector_swin_roi_passport_invoice",
            "processing_mode": request.processing_mode,
            "routing": routing,
            "template_id": template_id,
            "document_type": document_type,
            "engine": engine_name,
            "preprocessing": {
                "quality": quality,
                "transforms": transforms,
            },
            "layout": layout,
            "field_count": total,
            "validated_field_count": valid_count,
        }

        diagnostics.update(diagnostics_extra)

        # Keep the response readable when the same post-processing hook is applied
        # before and after field merging.
        cleaned_warnings = []
        seen_warnings = set()
        for warning in warnings:
            if warning in seen_warnings:
                continue
            seen_warnings.add(warning)
            cleaned_warnings.append(warning)
        warnings = cleaned_warnings

        return ExtractionResponse(
            job_id=job_id,
            status=status,
            template_id=template_id,
            document_type=document_type,
            document_variant=None,
            engine_used=engine_name,
            language_detected=language_detected,
            global_confidence=global_conf,
            quality_score=(quality or {}).get("quality_score"),
            fields=fields,
            normalized_data=normalized_data,
            routing=routing,
            business_validation=business_validation,
            diagnostics=diagnostics if request.include_diagnostics else None,
            raw_text=raw_text,
            processing_time_ms=int((time.perf_counter() - started) * 1000),
            warnings=warnings,
            review_reasons=business_validation["review_reasons"],
        )

    def _registry_warnings_from_fields(
        self,
        fields: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Recompute registry warnings after Paddle/EasyOCR field-level merge.

        This avoids keeping stale warnings from the primary OCR pass when a
        secondary OCR engine later fills the missing required fields.
        """
        required = {
            "identifiant_unique",
            "raison_sociale",
            "date_extrait",
        }

        by_name = {f.get("name"): f for f in fields}
        warnings: List[str] = []

        for name in sorted(required):
            field = by_name.get(name)

            if (
                not field
                or not field.get("validated")
                or field.get("value") in (None, "", [])
            ):
                warnings.append(
                    f"Required registry field '{name}' missing or invalid"
                )

        return warnings


    def _run_invoice_full_page_extraction(
        self,
        *,
        image,
        original_image,
        request: ExtractionRequest,
        template: Optional[Any],
        routing: Dict[str, Any],
        job_id: str,
        engine_name: str,
        language_hints: List[str],
        language_detected: str,
        quality: Dict[str, Any],
        transforms: Dict[str, Any],
        layout: Dict[str, Any],
        localizer_diagnostics: Dict[str, Any],
        started: float,
    ) -> ExtractionResponse:
        """
        Dedicated invoice path.

        Important:
        - full-page OCR is the only source for amounts;
        - targeted OCR is diagnostic and can fill invoice_number / invoice_date only;
        - targeted OCR is never merged into raw_text used for TVA, timbre, TTC or consistency.
        """
        if not engine_name or str(engine_name).strip().lower() == "auto":
            engine_name = "paddle"

        engine = get_engine_adapter(engine_name)
        raw_started = time.perf_counter()

        raw_ocr_image = image if image is not None else original_image
        raw_ocr_source = "full_page_invoice_ocr"

        raw_text, raw_score = call_recognize_document(
            engine,
            raw_ocr_image,
            language_hints,
        )

        raw_elapsed_ms = int((time.perf_counter() - raw_started) * 1000)
        raw_text = normalize_text(raw_text)

        language_detected = detect_language(
            raw_text,
            hint=request.language_hint,
        ) or language_detected

        invoice_fields, invoice_normalized, invoice_debug, invoice_warnings = (
            self._extract_invoice_from_raw_text(raw_text)
        )

        missing_header_fields = self._invoice_missing_header_fields(invoice_fields)

        processing_mode = str(
            getattr(request, "processing_mode", None)
            or getattr(request, "mode", None)
            or "balanced"
        ).strip().lower()

        targeted_enabled = processing_mode in {"balanced", "full", "debug", "diagnostic"}

        targeted_invoice_ocr: Dict[str, Any] = {
            "executed": False,
            "reason": (
                "disabled_or_not_needed"
                if missing_header_fields and not targeted_enabled
                else "not_needed"
            ),
            "combined_text": "",
            "zones": [],
            "zone_count": 0,
            "non_empty_zone_count": 0,
            "missing_header_fields": missing_header_fields,
            "policy": "header_fields_only_no_amount_merge",
        }

        if missing_header_fields and targeted_enabled and run_invoice_targeted_ocr is not None:
            targeted_started = time.perf_counter()

            # Très important :
            # On recharge d'abord l'image uploadée via request.metadata["file_path"].
            # Les diagnostics précédents ont montré que raw_ocr_image / original_image
            # peuvent ne pas contenir le vrai haut de facture.
            targeted_source_image, targeted_source_info = self._select_invoice_targeted_source_image(
                request=request,
                image=image,
                original_image=original_image,
                fallback_image=raw_ocr_image,
            )

            # EasyOCR fallback est volontairement limité aux modes full/debug/diagnostic.
            # Il sert uniquement aux champs d'en-tête :
            # - invoice_number
            # - invoice_date
            # Il ne doit jamais être utilisé pour les montants.
            easyocr_engine = None
            easyocr_init_error = None

            if processing_mode in {"debug", "diagnostic"}:
                try:
                    if str(engine_name).strip().lower() == "easyocr":
                        easyocr_engine = engine
                    else:
                        easyocr_engine = get_engine_adapter("easyocr")
                except Exception as exc:
                    easyocr_init_error = f"{type(exc).__name__}: {exc}"

            targeted_mode = "debug" if processing_mode == "diagnostic" else processing_mode

            targeted_invoice_ocr = run_invoice_targeted_ocr(
                image=targeted_source_image,
                engine=engine,
                language_hints=language_hints,
                recognize_fn=call_recognize_document,
                normalize_fn=normalize_text,
                mode=targeted_mode,
                missing_fields=missing_header_fields,
                easyocr_engine=easyocr_engine,
                easyocr_recognize_fn=call_recognize_document if easyocr_engine is not None else None,
                use_easyocr_fallback=easyocr_engine is not None,
                strict_ttn_zones=True,

                # Debug visuel des crops : activé seulement en full/debug/diagnostic.
                # Le dossier permet de vérifier si les crops contiennent Facture N° / Date.
                save_debug_crops=processing_mode in {"debug", "diagnostic"},
                debug_dir=f"debug/invoice_crops/{job_id}",
            )

            targeted_invoice_ocr["processing_time_ms"] = int(
                (time.perf_counter() - targeted_started) * 1000
            )
            targeted_invoice_ocr["triggered_by_missing_critical_fields"] = missing_header_fields
            targeted_invoice_ocr["policy"] = "header_fields_only_no_amount_merge"
            targeted_invoice_ocr["source_image"] = targeted_source_info
            targeted_invoice_ocr["easyocr_fallback"] = {
                "enabled": easyocr_engine is not None,
                "init_error": easyocr_init_error,
                "scope": "invoice_number_and_invoice_date_only",
            }

            targeted_fields: List[Dict[str, Any]] = []
            best_candidates = targeted_invoice_ocr.get("best_candidates") or {}

            # Chemin prioritaire :
            # utiliser les candidats structurés retournés par invoice_targeted_ocr.py.
            for field_name in ("invoice_number", "invoice_date"):
                candidate = best_candidates.get(field_name)

                if not candidate:
                    continue

                value = candidate.get("value")
                confidence = float(candidate.get("confidence", 0.0) or 0.0)

                if value in (None, "", []) or confidence < 0.60:
                    continue

                targeted_fields.append(
                    {
                        "name": field_name,
                        "label": self._invoice_label_fr(field_name),
                        "value": value,
                        "confidence": round(confidence, 3),
                        "validated": True,
                        "raw_text": candidate.get("raw"),
                        "raw_template_field": field_name,
                        "error": None,
                        "selected_engine": candidate.get("engine_name"),
                        "selected_source": "invoice_targeted_header_ocr",
                        "review_required": False,
                        "reasons": [
                            "selected_from:invoice_targeted_header_ocr",
                            f"zone:{candidate.get('zone_id')}",
                            f"variant:{candidate.get('variant_id')}",
                            f"engine:{candidate.get('engine_name')}",
                            "targeted_scope:header_only",
                            f"source_image:{targeted_source_info.get('selected')}",
                        ],
                    }
                )

            targeted_text = str(targeted_invoice_ocr.get("combined_text") or "").strip()
            targeted_debug: Dict[str, Any] = {
                "structured_candidates_used": bool(targeted_fields),
                "combined_text_fallback_used": False,
                "source_image": targeted_source_info,
            }

            # Fallback rétrocompatible :
            # Si les candidats structurés sont vides, on parse combined_text.
            # Le merge reste sécurisé : _merge_invoice_targeted_header_fields
            # n'accepte que invoice_number et invoice_date.
            if not targeted_fields and targeted_text:
                parsed_targeted_fields, _, parsed_targeted_debug, _ = self._extract_invoice_from_raw_text(
                    targeted_text
                )

                targeted_fields = parsed_targeted_fields
                targeted_debug = {
                    "structured_candidates_used": False,
                    "combined_text_fallback_used": True,
                    "parsed_targeted_debug": parsed_targeted_debug,
                    "source_image": targeted_source_info,
                }

            if targeted_fields:
                invoice_fields = self._merge_invoice_targeted_header_fields(
                    base_fields=invoice_fields,
                    targeted_fields=targeted_fields,
                )
                invoice_normalized = self._invoice_normalized_from_invoice_fields(invoice_fields)
                invoice_warnings = self._invoice_warnings_from_fields(invoice_fields)

                invoice_debug["targeted_header_merge"] = {
                    "executed": True,
                    "policy": "invoice_number_and_invoice_date_only",
                    "missing_before": missing_header_fields,
                    "missing_after": self._invoice_missing_header_fields(invoice_fields),
                    "structured_best_candidates": best_candidates,
                    "targeted_debug": targeted_debug,
                }
            else:
                invoice_debug["targeted_header_merge"] = {
                    "executed": False,
                    "policy": "invoice_number_and_invoice_date_only",
                    "missing_before": missing_header_fields,
                    "missing_after": self._invoice_missing_header_fields(invoice_fields),
                    "reason": "no_valid_targeted_header_candidate",
                    "structured_best_candidates": best_candidates,
                    "targeted_debug": targeted_debug,
                }

        routing["selected_document_type"] = "invoice"
        routing["selected_template_id"] = (
            self._get(template, "id")
            or routing.get("selected_template_id")
            or getattr(request, "template_id", None)
            or "invoice_tn"
        )

        return self._build_response(
            job_id=job_id,
            request=request,
            template=template,
            routing=routing,
            raw_text=raw_text,
            fields=invoice_fields,
            normalized_data=invoice_normalized,
            language_detected=language_detected,
            engine_name=engine_name,
            quality=quality,
            transforms=transforms,
            layout=layout,
            diagnostics_extra={
                "document_localizer": localizer_diagnostics,
                "raw_ocr_executed": True,
                "raw_ocr_source": raw_ocr_source,
                "raw_ocr_score": raw_score,
                "raw_ocr_processing_time_ms": raw_elapsed_ms,
                "invoice_targeted_ocr": targeted_invoice_ocr,
                "invoice_text_enriched": False,
                "invoice_targeted_policy": "diagnostic_and_header_fields_only",
                "invoice_extraction": invoice_debug,
                "strategy": "invoice_raw_text_rules",
                "roi_skipped": True,
                "roi_skipped_reason": "invoice_full_page_ocr_path",
            },
            warnings=invoice_warnings,
            started=started,
        )

    def _filter_registry_response_fields(
    self,
    fields: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
        """
        Pour les registres :
        - garder toujours les champs critiques de la variante détectée ;
        - garder les champs optionnels seulement s'ils sont validés ;
        - éviter que les champs optionnels vides fassent tomber le statut en partial.
        """
        critical = self._registry_critical_field_names(fields)

        cleaned: List[Dict[str, Any]] = []

        for field in fields:
            name = str(field.get("name") or "")
            value = field.get("value")
            validated = bool(field.get("validated"))

            if name in critical:
                cleaned.append(field)
                continue

            if validated and value not in (None, "", []):
                cleaned.append(field)

        return cleaned
    
    def _merge_registry_layout_fields(
        self,
        base_fields: List[Dict[str, Any]],
        layout_fields: Dict[str, Dict[str, Any]],
        *,
        engine_name: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Ajoute ou corrige les champs optionnels extraits par OCR zonée RNE.

        Politique :
        - ne remplace jamais les champs critiques ;
        - ajoute les champs optionnels validés ;
        - peut remplacer certains champs optionnels structurés si la valeur zonée est meilleure ;
        - reste prudent sur adresse / activité / forme juridique.
        """
        optional_allowed = {
            "numero_registre",
            "numero_depot",
            "numero_interne",
            "nom_commercial",
            "adresse_sociale",
            "activite_principale",
            "activite_secondaire",
            "forme_juridique",
            "dirigeant_qualite",
            "dirigeant_adresse",
            "dirigeant_nationalite",
            "dirigeant_date_naissance",
            "dirigeant_nom_prenom",
        }

        critical = self._registry_critical_field_names(base_fields)

        # Champs optionnels pour lesquels une valeur zonée peut corriger
        # une valeur globale déjà présente.
        replaceable_optional = {
            "dirigeant_date_naissance",
            "dirigeant_nationalite",
            "dirigeant_qualite",
        }

        by_name: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for field in base_fields:
            name = str(field.get("name") or "")
            if not name:
                continue

            by_name[name] = dict(field)

            if name not in order:
                order.append(name)

        added: List[str] = []
        replaced: List[str] = []
        ignored: List[str] = []

        for name, payload in (layout_fields or {}).items():
            if name in critical:
                ignored.append(name)
                continue

            if name not in optional_allowed:
                ignored.append(name)
                continue

            value = payload.get("value")
            if value in (None, "", []):
                ignored.append(name)
                continue

            old = by_name.get(name)

            new_confidence = round(float(payload.get("confidence", 0.50) or 0.50), 3)

            new_field = {
                "name": name,
                "value": value,
                "confidence": new_confidence,
                "validated": True,
                "raw_text": payload.get("raw_text"),
                "raw_template_field": name,
                "error": None,
                "selected_engine": engine_name,
                "selected_source": "rne_layout_zone_ocr",
                "review_required": False,
                "reasons": [
                    "selected_from:rne_layout_zone_ocr",
                    f"source_zone:{payload.get('source_zone')}",
                    f"ocr_engine:{engine_name}",
                ],
            }

            if old and old.get("validated") and old.get("value") not in (None, "", []):
                old_value = old.get("value")

                # Cas spécial : date de naissance dirigeant.
                # La zone direction est plus fiable que le texte pleine page,
                # car elle évite de confondre avec d'autres dates administratives.
                if name in replaceable_optional and str(old_value) != str(value):
                    new_field["reasons"].append(f"replaced_previous_value:{old_value}")
                    by_name[name] = self._dedupe_reasons(new_field)
                    replaced.append(name)
                    continue

                ignored.append(name)
                continue

            by_name[name] = self._dedupe_reasons(new_field)

            if name not in order:
                order.append(name)

            added.append(name)

        debug = {
            "policy": "optional_valid_layout_fields_with_structured_optional_replace",
            "added_fields": sorted(set(added)),
            "replaced_fields": sorted(set(replaced)),
            "ignored_fields": sorted(set(ignored)),
            "replaceable_optional": sorted(replaceable_optional),
            "optional_allowed": sorted(optional_allowed),
        }

        return [by_name[name] for name in order if name in by_name], debug

    def _run_registry_full_page_extraction(
    self,
    *,
    image,
    original_image,
    request: ExtractionRequest,
    template: Optional[Any],
    routing: Dict[str, Any],
    job_id: str,
    engine_name: str,
    language_hints: List[str],
    language_detected: str,
    quality: Dict[str, Any],
    transforms: Dict[str, Any],
    layout: Dict[str, Any],
    localizer_diagnostics: Dict[str, Any],
    started: float,
) -> ExtractionResponse:
        """
        Dedicated Tunisian RNE / registre de commerce path.

        Important:
        - full-page OCR is required;
        - YOLO crop is skipped;
        - ROI, passport, MRZ and CIN paths are skipped;
        - PaddleOCR and EasyOCR are never concatenated as raw_text;
        - extraction is performed separately on each OCR text;
        - merge is done field by field using RegistreCommerceExtractor.
        """
        if not engine_name or str(engine_name).strip().lower() == "auto":
            engine_name = "paddle"

        primary_engine_name = str(engine_name).strip().lower()
        secondary_engine_name = "easyocr" if primary_engine_name != "easyocr" else "paddle"

        raw_ocr_image = image if image is not None else original_image

        # ------------------------------------------------------------------
        # 1. OCR primaire pleine page
        # ------------------------------------------------------------------
        primary_engine = get_engine_adapter(primary_engine_name)
        primary_started = time.perf_counter()

        primary_text, primary_score = call_recognize_document(
            primary_engine,
            raw_ocr_image,
            language_hints,
        )

        primary_elapsed_ms = int((time.perf_counter() - primary_started) * 1000)
        primary_text = normalize_text(primary_text)

        language_detected = detect_language(
            primary_text,
            hint=request.language_hint,
        ) or language_detected

        # ------------------------------------------------------------------
        # 2. Extraction spécialisée RNE sur OCR primaire
        # ------------------------------------------------------------------
        registry_extractor = self.registre_commerce_extractor or RegistreCommerceExtractor()

        try:
            primary_field_outputs = registry_extractor.extract(
                primary_text,
                metadata={
                    "source": "primary_registry_ocr",
                    "engine": primary_engine_name,
                },
            )
        except TypeError:
            primary_field_outputs = registry_extractor.extract(primary_text)

        primary_fields = self._registry_field_outputs_to_dicts(
            primary_field_outputs,
            engine_name=primary_engine_name,
            source="primary_registry_specialized_extractor",
        )

        missing_critical_before_secondary = self._registry_missing_critical_fields(primary_fields)

        processing_mode = str(
            getattr(request, "processing_mode", None)
            or getattr(request, "mode", None)
            or "balanced"
        ).strip().lower()

        run_secondary_for_consensus = processing_mode in {
            "full",
            "debug",
            "diagnostic",
        }

        should_run_secondary = bool(missing_critical_before_secondary) or run_secondary_for_consensus

        secondary_text = ""
        secondary_score = None
        secondary_elapsed_ms = None
        secondary_fields: List[Dict[str, Any]] = []

        merge_debug: Dict[str, Any] = {
            "enabled": True,
            "executed": False,
            "primary_engine": primary_engine_name,
            "secondary_engine": secondary_engine_name,
            "reason": "not_needed",
            "missing_critical_before": missing_critical_before_secondary,
            "policy": "specialized_extractor_field_level_merge_no_raw_text_concat",
        }

        # ------------------------------------------------------------------
        # 3. OCR secondaire EasyOCR/Paddle si nécessaire
        # ------------------------------------------------------------------
        if should_run_secondary:
            try:
                secondary_engine = get_engine_adapter(secondary_engine_name)
                secondary_started = time.perf_counter()

                secondary_text, secondary_score = call_recognize_document(
                    secondary_engine,
                    raw_ocr_image,
                    language_hints,
                )

                secondary_elapsed_ms = int((time.perf_counter() - secondary_started) * 1000)
                secondary_text = normalize_text(secondary_text)

                try:
                    secondary_field_outputs = registry_extractor.extract(
                        secondary_text,
                        metadata={
                            "source": "secondary_registry_ocr",
                            "engine": secondary_engine_name,
                        },
                    )
                except TypeError:
                    secondary_field_outputs = registry_extractor.extract(secondary_text)

                secondary_fields = self._registry_field_outputs_to_dicts(
                    secondary_field_outputs,
                    engine_name=secondary_engine_name,
                    source="secondary_registry_specialized_extractor",
                )

                # merge_registre_fields travaille sur FieldOutput, donc on fusionne
                # les FieldOutput originaux puis on reconvertit en dicts API.
                merged_field_outputs = merge_registre_fields(
                    primary_field_outputs,
                    secondary_field_outputs,
                )

                text_fields = self._registry_field_outputs_to_dicts(
                    merged_field_outputs,
                    engine_name=f"{primary_engine_name}+{secondary_engine_name}",
                    source="registry_specialized_field_merge",
                )

                missing_after = self._registry_missing_critical_fields(text_fields)

                merge_debug = {
                    "enabled": True,
                    "executed": True,
                    "reason": (
                        "missing_critical_fields"
                        if missing_critical_before_secondary
                        else "full_debug_consensus_check"
                    ),
                    "primary_engine": primary_engine_name,
                    "secondary_engine": secondary_engine_name,
                    "primary_score": primary_score,
                    "secondary_score": secondary_score,
                    "primary_text_length": len(primary_text or ""),
                    "secondary_text_length": len(secondary_text or ""),
                    "primary_processing_time_ms": primary_elapsed_ms,
                    "secondary_processing_time_ms": secondary_elapsed_ms,
                    "missing_critical_before": missing_critical_before_secondary,
                    "missing_critical_after": missing_after,
                    "primary_valid_fields": sum(1 for f in primary_fields if f.get("validated")),
                    "secondary_valid_fields": sum(1 for f in secondary_fields if f.get("validated")),
                    "primary_present_fields": sum(
                        1 for f in primary_fields if f.get("value") not in (None, "", [])
                    ),
                    "secondary_present_fields": sum(
                        1 for f in secondary_fields if f.get("value") not in (None, "", [])
                    ),
                    "policy": "RegistreCommerceExtractor + merge_registre_fields",
                }

            except Exception as exc:
                text_fields = primary_fields
                merge_debug = {
                    "enabled": True,
                    "executed": False,
                    "reason": "secondary_ocr_failed",
                    "primary_engine": primary_engine_name,
                    "secondary_engine": secondary_engine_name,
                    "missing_critical_before": missing_critical_before_secondary,
                    "error": f"{type(exc).__name__}: {exc}",
                    "policy": "keep_primary_specialized_result_on_secondary_failure",
                }

        else:
            text_fields = primary_fields

        # ------------------------------------------------------------------
        # 4. OCR zonée RNE pour champs optionnels
        # ------------------------------------------------------------------
        layout_ocr_debug: Dict[str, Any] = {
            "executed": False,
            "reason": "disabled_or_not_available",
        }

        processing_mode = str(
            getattr(request, "processing_mode", None)
            or getattr(request, "mode", None)
            or "balanced"
        ).strip().lower()

        # L'OCR zonée est volontairement limitée à full/debug/diagnostic.
        # En balanced, on garde le résultat rapide et stable.
        run_layout_ocr = processing_mode in {"full", "debug", "diagnostic"}

        if run_layout_ocr and run_rne_layout_ocr is not None:
            try:
                layout_engine_name = primary_engine_name
                layout_engine = get_engine_adapter(layout_engine_name)

                layout_ocr_debug = run_rne_layout_ocr(
                    image_bgr=raw_ocr_image,
                    engine=layout_engine,
                    engine_name=layout_engine_name,
                    language_hints=language_hints,
                    recognize_fn=call_recognize_document,
                    normalize_fn=normalize_text,
                )

                layout_fields = layout_ocr_debug.get("fields") or {}

                text_fields, layout_merge_debug = self._merge_registry_layout_fields(
                    base_fields=text_fields,
                    layout_fields=layout_fields,
                    engine_name=layout_engine_name,
                )

                layout_ocr_debug["merge"] = layout_merge_debug

            except Exception as exc:
                layout_ocr_debug = {
                    "executed": False,
                    "reason": "layout_ocr_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # ------------------------------------------------------------------
        # 5. Filtrer les champs de réponse
        # ------------------------------------------------------------------
        text_fields = self._filter_registry_response_fields(text_fields)

        text_normalized = self._normalized_from_fields_with_template(
            text_fields,
            template,
        )

        valid_count = sum(1 for f in text_fields if f.get("validated"))
        present_count = sum(1 for f in text_fields if f.get("value") not in (None, "", []))

        routing["selected_document_type"] = "registre_commerce"
        routing["selected_template_id"] = (
            self._get(template, "id")
            or routing.get("selected_template_id")
            or getattr(request, "template_id", None)
            or "registre_commerce_tn"
        )

        registry_debug: Dict[str, Any] = {
            "strategy": "specialized_registry_extractor_full_page_ocr",
            "extractor": "RegistreCommerceExtractor",
            "generic_template_extractor_used": False,
            "valid_fields": valid_count,
            "present_fields": present_count,
            "ocr_merge": merge_debug,
            "layout_ocr": layout_ocr_debug,
            "field_policy": {
                "critical_fields": sorted(self._registry_critical_field_names(text_fields)),
                "optional_fields": [
                    "nom_commercial",
                    "adresse_sociale",
                    "activite_principale",
                    "activite_secondaire",
                    "forme_juridique",
                    "dirigeant_qualite",
                    "dirigeant_adresse",
                    "dirigeant_nationalite",
                    "dirigeant_date_naissance",
                    "dirigeant_nom_prenom",
                    "numero_registre",
                    "numero_depot",
                    "numero_interne",
                ],
                "optional_fields_do_not_block_success": True,
            },
        }

        return self._build_response(
            job_id=job_id,
            request=request,
            template=template,
            routing=routing,
            raw_text=primary_text,
            fields=text_fields,
            normalized_data=text_normalized,
            language_detected=language_detected,
            engine_name=primary_engine_name,
            quality=quality,
            transforms=transforms,
            layout=layout,
            diagnostics_extra={
                "document_localizer": localizer_diagnostics,
                "raw_ocr_executed": True,
                "raw_ocr_source": f"full_page_registry_ocr_{primary_engine_name}",
                "raw_ocr_score": primary_score,
                "raw_ocr_processing_time_ms": primary_elapsed_ms,
                "secondary_raw_ocr_executed": bool(merge_debug.get("executed")),
                "secondary_raw_ocr_source": (
                    f"full_page_registry_ocr_{secondary_engine_name}"
                    if merge_debug.get("executed")
                    else None
                ),
                "secondary_raw_ocr_score": secondary_score,
                "secondary_raw_ocr_processing_time_ms": secondary_elapsed_ms,
                "registry_extraction": registry_debug,
                "strategy": "registry_full_page_ocr_specialized_field_level_merge",
                "roi_skipped": True,
                "roi_skipped_reason": "registry_full_page_ocr_path",
                "passport_skipped": True,
                "passport_skipped_reason": "registry_full_page_ocr_path",
            },
            warnings=self._registry_warnings_from_fields(text_fields),
            started=started,
        )


    def run(
        self,
        prep: Dict[str, Any],
        request: ExtractionRequest,
        job_id: str,
        template: Optional[Any] = None,
    ) -> ExtractionResponse:
        started = time.perf_counter()

        image = prep["image"]
        processing_mode = (request.processing_mode or "balanced").strip().lower()

        original_image = self._image_for_document_localization(
            prep=prep,
            request=request,
            fallback_image=image,
        )

        quality = prep.get("quality", {})
        transforms = prep.get("transforms", {})
        layout = prep.get("layout", {})

        # Fast explicit invoice path:
        # If the request explicitly declares an invoice, skip document localization,
        # passport and ROI completely. This prevents invoice images from being
        # processed by an ID/passport template and avoids unnecessary YOLO/ROI cost.
        if self._request_declares_invoice(
            request=request,
            template=template,
            routing=None,
        ):
            routing: Dict[str, Any] = {
                "requested_document_type": request.document_type,
                "requested_template_id": request.template_id,
                "selected_template_id": self._get(template, "id") or request.template_id or "invoice_tn",
                "selected_document_type": "invoice",
                "method": "explicit_invoice_request_fast_path",
                "forced_invoice_guard": True,
            }

            template, routing = self._force_invoice_template_if_requested(
                request=request,
                template=template,
                routing=routing,
            )

            if template is None:
                template = self._safe_get_template(request.template_id or "invoice_tn")
                routing["selected_template_id"] = self._get(template, "id") or request.template_id or "invoice_tn"

            engine_name = self._select_engine_name(request, template)
            if not engine_name or str(engine_name).strip().lower() == "auto":
                engine_name = "paddle"

            language_hints = self._language_hints(
                request=request,
                template=template,
            )

            return self._run_invoice_full_page_extraction(
                image=image,
                original_image=original_image,
                request=request,
                template=template,
                routing=routing,
                job_id=job_id,
                engine_name=engine_name,
                language_hints=language_hints,
                language_detected=request.language_hint or "auto",
                quality=quality,
                transforms=transforms,
                layout=layout,
                localizer_diagnostics={
                    "localizer": "skipped",
                    "reason": "explicit_invoice_full_page_ocr_fast_path",
                    "candidate_count": 0,
                },
                started=started,
            )

        # Fast explicit registry path:
        # If the request explicitly declares a Tunisian business registry / RNE,
        # skip document localization, passport, ID and ROI completely.
        # This prevents YOLO from cropping only the bottom part of the RNE document.
        if self._request_declares_registry(
            request=request,
            template=template,
            routing=None,
        ):
            routing: Dict[str, Any] = {
                "requested_document_type": request.document_type,
                "requested_template_id": request.template_id,
                "selected_template_id": self._get(template, "id") or request.template_id or "registre_commerce_tn",
                "selected_document_type": "registre_commerce",
                "method": "explicit_registry_request_fast_path",
                "forced_registry_guard": True,
            }

            template, routing = self._force_registry_template_if_requested(
                request=request,
                template=template,
                routing=routing,
            )

            if template is None:
                template = self._safe_get_template(request.template_id or "registre_commerce_tn")
                routing["selected_template_id"] = (
                    self._get(template, "id")
                    or request.template_id
                    or "registre_commerce_tn"
                )

            engine_name = self._select_engine_name(request, template)

            if not engine_name or str(engine_name).strip().lower() == "auto":
                engine_name = "paddle"

            language_hints = self._language_hints(
                request=request,
                template=template,
            )

            return self._run_registry_full_page_extraction(
                image=image,
                original_image=original_image,
                request=request,
                template=template,
                routing=routing,
                job_id=job_id,
                engine_name=engine_name,
                language_hints=language_hints,
                language_detected=request.language_hint or "auto",
                quality=quality,
                transforms=transforms,
                layout=layout,
                localizer_diagnostics={
                    "localizer": "skipped",
                    "reason": "explicit_registry_full_page_ocr_fast_path",
                    "candidate_count": 0,
                },
                started=started,
            )


        localized_image, localized_candidates, localizer_diagnostics = self._localize_document_safely(
            original_image=original_image,
            fallback_image=image,
            processing_mode=processing_mode,
        )

        quality = prep.get("quality", {})
        transforms = prep.get("transforms", {})
        layout = prep.get("layout", {})

        template, routing = self._resolve_template_before_ocr(
            request=request,
            explicit_template=template,
            classification_image=localized_image,
        )
          # Strong invoice guard:
        # If the request says invoice, never let ROI/passport/classifier redirect it
        # to an ID/passport template.
        template, routing = self._force_invoice_template_if_requested(
            request=request,
            template=template,
            routing=routing,
        )

        template, routing = self._force_registry_template_if_requested(
            request=request,
            template=template,
            routing=routing,
        )

        engine_name = self._select_engine_name(request, template)

        language_hints = self._language_hints(
            request=request,
            template=template,
        )

        language_detected = request.language_hint or "auto"

        # Routed/classified invoice path:
        # If classification or template resolution selected an invoice after
        # localization, still skip passport/ROI and use full-page invoice OCR.
        if self._request_declares_invoice(
            request=request,
            template=template,
            routing=routing,
        ):
            return self._run_invoice_full_page_extraction(
                image=image,
                original_image=original_image,
                request=request,
                template=template,
                routing=routing,
                job_id=job_id,
                engine_name=engine_name,
                language_hints=language_hints,
                language_detected=language_detected,
                quality=quality,
                transforms=transforms,
                layout=layout,
                localizer_diagnostics=localizer_diagnostics,
                started=started,
            )

        # Routed/classified registry path:
        # If classification or template resolution selected a Tunisian business
        # registry after localization, still skip passport/ROI and use full-page OCR.
        if self._request_declares_registry(
            request=request,
            template=template,
            routing=routing,
        ):
            return self._run_registry_full_page_extraction(
                image=image,
                original_image=original_image,
                request=request,
                template=template,
                routing=routing,
                job_id=job_id,
                engine_name=engine_name,
                language_hints=language_hints,
                language_detected=language_detected,
                quality=quality,
                transforms=transforms,
                layout=layout,
                localizer_diagnostics=localizer_diagnostics,
                started=started,
            )

        if self._is_passport_template_or_route(
            template=template,
            routing=routing,
            request=request,
        ):
            use_full_image_for_passport, passport_width_ratio = (
                _should_use_full_image_for_passport_mrz(
                    original_image=original_image,
                    localized_image=localized_image,
                    min_width_ratio=0.90,
                )
            )

            passport_width_guard = {
                "enabled": True,
                "min_width_ratio": 0.90,
                "original_width": _safe_image_width(original_image),
                "localized_width": _safe_image_width(localized_image),
                "actual_width_ratio": (
                    round(passport_width_ratio, 4)
                    if passport_width_ratio is not None
                    else None
                ),
                "use_full_image_candidate": bool(use_full_image_for_passport),
                "candidate_policy": (
                    "full_image_prepended"
                    if use_full_image_for_passport
                    else "localized_candidates_only"
                ),
            }

            localized_candidates = localized_candidates or []
            raw_candidates = [
                c for c in localized_candidates
                if "normalized" not in str(c.get("source") or "").lower()
            ]
            normalized_candidates = [
                c for c in localized_candidates
                if "normalized" in str(c.get("source") or "").lower()
            ]
            # Évite de tester trop de crops normalisés en mode fast.
            # Les crops normalisés sont utiles en fallback, mais peuvent coûter cher
            # quand aucune MRZ valide n'est trouvée.
            if processing_mode == "fast":
                normalized_candidates = normalized_candidates[:2]
            elif processing_mode == "balanced":
                normalized_candidates = normalized_candidates[:4]

            passport_candidates: List[Dict[str, Any]] = []

            # In fast mode, try the safest/highest value candidates first.
            # yolo_crop_normalized candidates are kept as fallback only: they are
            # reached only if full_image/yolo_crop_raw do not produce a valid MRZ,
            # because _extract_passport_best_candidate stops immediately on valid MRZ.
            if use_full_image_for_passport:
                passport_candidates.append(
                    {
                        "image": original_image,
                        "angle": 0,
                        "candidate_index": -1,
                        "rotation_index": None,
                        "source": "full_image_width_guard",
                    }
                )

            if raw_candidates:
                passport_candidates.extend(raw_candidates)
            elif localized_image is not None:
                passport_candidates.append(
                    {
                        "image": localized_image,
                        "angle": 0,
                        "candidate_index": 0,
                        "rotation_index": None,
                        "source": "yolo_crop_raw_fallback",
                    }
                )

            passport_candidates.extend(normalized_candidates)

            passport_fields, passport_normalized, passport_debug, passport_warnings = (
                self._extract_passport_best_candidate(
                    image=original_image if use_full_image_for_passport else localized_image,
                    engine_name="easyocr",
                    language_hints=["en"],
                    localized_candidates=passport_candidates,
                    stop_on_valid_mrz=True,
                )
            )

            passport_width_guard["selected_source"] = passport_debug.get("selected_source")
            passport_width_guard["selected_candidate_index"] = passport_debug.get(
                "selected_candidate_index"
            )

            if passport_fields:
                valid_count = sum(1 for f in passport_fields if f.get("validated"))

                if processing_mode == "fast" or valid_count >= 5:
                    raw_text = (
                        passport_debug.get("selected_candidate_debug", {})
                        .get("selected_raw_text", "")
                    )

                    return self._build_response(
                        job_id=job_id,
                        request=request,
                        template=template,
                        routing=routing,
                        raw_text=raw_text,
                        fields=passport_fields,
                        normalized_data=passport_normalized,
                        language_detected="en",
                        engine_name="passport_mrz",
                        quality=quality,
                        transforms=transforms,
                        layout=layout,
                        diagnostics_extra={
                            "document_localizer": localizer_diagnostics,
                            "passport_extraction": passport_debug,
                            "passport_width_guard": passport_width_guard,
                            "raw_ocr_executed": False,
                            "strategy": "passport_mrz_first",
                        },
                        warnings=passport_warnings,
                        started=started,
                    )

        roi_fields: List[Dict[str, Any]] = []
        roi_normalized: Dict[str, Any] = {}
        roi_debug: Dict[str, Any] = {}
        roi_warnings: List[str] = []
        roi_width_guard: Dict[str, Any] = {
            "enabled": False,
            "reason": "not_roi_template_or_not_executed",
        }

        if template is not None and self._has_roi_fields(template):
            use_full_image_for_roi, roi_width_ratio = _should_use_full_image_for_roi_template(
                original_image=original_image,
                localized_image=localized_image,
                min_width_ratio=0.90,
            )

            roi_width_guard = {
                "enabled": True,
                "min_width_ratio": 0.90,
                "original_width": _safe_image_width(original_image),
                "localized_width": _safe_image_width(localized_image),
                "actual_width_ratio": (
                    round(roi_width_ratio, 4)
                    if roi_width_ratio is not None
                    else None
                ),
                "use_full_image_candidate": bool(use_full_image_for_roi),
                "candidate_policy": (
                    "full_image_prepended"
                    if use_full_image_for_roi
                    else "localized_candidates_only"
                ),
            }

            roi_candidates = localized_candidates

            if use_full_image_for_roi:
                roi_candidates = [
                    {
                        "image": original_image,
                        "angle": 0,
                        "candidate_index": -1,
                        "rotation_index": None,
                        "source": "full_image_roi_width_guard",
                        "content_bbox_norm": None,
                    }
                ]

                if localized_candidates:
                    roi_candidates.extend(localized_candidates)
                elif localized_image is not None:
                    roi_candidates.append(
                        {
                            "image": localized_image,
                            "angle": 0,
                            "candidate_index": 0,
                            "rotation_index": None,
                            "source": "yolo_crop_after_roi_width_guard",
                            "content_bbox_norm": None,
                        }
                    )

            roi_fields, roi_normalized, roi_debug, roi_warnings = self._extract_roi_best_candidate(
                image=original_image if use_full_image_for_roi else localized_image,
                template=template,
                engine_name=engine_name,
                language_hints=language_hints,
                processing_mode=processing_mode,
                localized_candidates=roi_candidates,
            )

            template_id_for_postprocess = str(self._get(template, "id") or "")

            if template_id_for_postprocess == "midv_svk_id":
                roi_fields = self._postprocess_midv_svk_id_fields(roi_fields)
                roi_fields, roi_normalized = self._filter_non_text_visual_fields(
                    roi_fields,
                    roi_normalized,
                )
                roi_normalized = self._normalized_from_fields(roi_fields)

            roi_width_guard["selected_source"] = roi_debug.get("selected_source")
            roi_width_guard["selected_candidate_index"] = roi_debug.get("selected_candidate_index")
            roi_width_guard["selected_angle"] = roi_debug.get("selected_angle")
            roi_width_guard["selected_score"] = roi_debug.get("selected_score")

            skip_raw_ocr_for_strong_roi = (
                processing_mode in {"fast", "balanced"}
                and not self._is_invoice_template_or_route(
                    template=template,
                    routing=routing,
                    request=request,
                )
                and self._roi_result_is_strong_enough(roi_fields, roi_debug)
            )

            if processing_mode == "fast" or skip_raw_ocr_for_strong_roi:
                return self._build_response(
                    job_id=job_id,
                    request=request,
                    template=template,
                    routing=routing,
                    raw_text="",
                    fields=roi_fields,
                    normalized_data=roi_normalized,
                    language_detected=language_detected,
                    engine_name=engine_name,
                    quality=quality,
                    transforms=transforms,
                    layout=layout,
                    diagnostics_extra={
                        "document_localizer": localizer_diagnostics,
                        "roi_extraction": roi_debug,
                        "roi_width_guard": roi_width_guard,
                        "raw_ocr_executed": False,
                        "raw_ocr_skipped_reason": (
                            "strong_roi_candidate"
                            if skip_raw_ocr_for_strong_roi
                            else "fast_mode"
                        ),
                    },
                    warnings=roi_warnings,
                    started=started,
                )

        engine = get_engine_adapter(engine_name)

        raw_started = time.perf_counter()

        if self._is_invoice_template_or_route(
            template=template,
            routing=routing,
            request=request,
        ):
            # Factures tunisiennes: toujours OCR pleine page.
            # Le localizer YOLO peut sélectionner uniquement le bas/tableau de la
            # facture; cela fait perdre le numéro, la date, la référence unique,
            # le fournisseur et parfois le total. On garde donc la page entière.
            # `image` correspond à l'image prétraitée pleine page, généralement
            # meilleure pour PaddleOCR que l'image brute originale.
            raw_ocr_image = image if image is not None else original_image
            raw_ocr_source = "full_page_invoice_ocr"
        else:
            raw_ocr_image = localized_image if template is not None else image
            raw_ocr_source = (
                "localized_document_ocr"
                if template is not None
                else "full_page_no_template_ocr"
            )

        raw_text, raw_score = call_recognize_document(
            engine,
            raw_ocr_image,
            language_hints,
        )

        raw_elapsed_ms = int((time.perf_counter() - raw_started) * 1000)
        raw_text = normalize_text(raw_text)

        language_detected = detect_language(
            raw_text,
            hint=request.language_hint,
        ) or language_detected

        template, routing = self._resolve_template_after_ocr(
            request=request,
            raw_text=raw_text,
            current_template=template,
            routing=routing,
            classification_image=localized_image,
        )

        if self._is_invoice_template_or_route(
            template=template,
            routing=routing,
            request=request,
        ):
            invoice_fields, invoice_normalized, invoice_debug, invoice_warnings = (
                self._extract_invoice_from_raw_text(raw_text)
            )

            return self._build_response(
                job_id=job_id,
                request=request,
                template=template,
                routing=routing,
                raw_text=raw_text,
                fields=invoice_fields,
                normalized_data=invoice_normalized,
                language_detected=language_detected,
                engine_name=engine_name,
                quality=quality,
                transforms=transforms,
                layout=layout,
                diagnostics_extra={
                    "document_localizer": localizer_diagnostics,
                    "raw_ocr_executed": True,
                    "raw_ocr_source": raw_ocr_source,
                    "raw_ocr_score": raw_score,
                    "raw_ocr_processing_time_ms": raw_elapsed_ms,
                    "invoice_extraction": invoice_debug,
                    "strategy": "invoice_raw_text_rules",
                },
                warnings=invoice_warnings,
                started=started,
            )

        text_fields, text_normalized, text_debug, text_warnings = self.text_extractor.extract(
            raw_text=raw_text,
            template=template,
            document_type=routing.get("selected_document_type") or request.document_type,
            language=language_detected,
        )

        merged_fields = self._merge_field_results(
            roi_fields,
            text_fields,
        )

        template_id_for_postprocess = str(self._get(template, "id") or "")

        if template_id_for_postprocess == "midv_svk_id":
            merged_fields = self._postprocess_midv_svk_id_fields(merged_fields)

        normalized_data: Dict[str, Any] = {}
        normalized_data.update(text_normalized)
        normalized_data.update(roi_normalized)

        if not normalized_data:
            normalized_data = self._normalized_from_fields(merged_fields)

        if template_id_for_postprocess == "midv_svk_id":
            merged_fields, normalized_data = self._filter_non_text_visual_fields(
                merged_fields,
                normalized_data,
            )
            normalized_data = self._normalized_from_fields(merged_fields)

        warnings: List[str] = []
        warnings.extend(roi_warnings)
        warnings.extend(text_warnings)

        return self._build_response(
            job_id=job_id,
            request=request,
            template=template,
            routing=routing,
            raw_text=raw_text,
            fields=merged_fields,
            normalized_data=normalized_data,
            language_detected=language_detected,
            engine_name=engine_name,
            quality=quality,
            transforms=transforms,
            layout=layout,
            diagnostics_extra={
                "document_localizer": localizer_diagnostics,
                "roi_extraction": roi_debug,
                "roi_width_guard": roi_width_guard,
                "raw_ocr_executed": True,
                "raw_ocr_source": raw_ocr_source,
                "raw_ocr_score": raw_score,
                "raw_ocr_processing_time_ms": raw_elapsed_ms,
                "text_extraction": text_debug,
            },
            warnings=warnings,
            started=started,
        )

    def _run_explicit_template(
        self,
        prep: Dict[str, Any],
        request: ExtractionRequest,
        job_id: str,
    ) -> ExtractionResponse:
        template = self._safe_get_template(request.template_id)

        return self.run(
            prep=prep,
            request=request,
            template=template,
            job_id=job_id,
        )

    def _run_no_template(
        self,
        prep: Dict[str, Any],
        request: ExtractionRequest,
        job_id: str,
    ) -> ExtractionResponse:
        return self.run(
            prep=prep,
            request=request,
            template=None,
            job_id=job_id,
        )