# Common utilities for the document processing pipeline.
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path
from typing import Union

import cv2
import numpy as np

from app.core.logging import get_logger
from app.pipeline.io import load_file_as_pages
from app.pipeline.preprocess import preprocess_for_pipeline
from app.models.layout.layout_detector import get_layout_detector
from app.services.engine_selector import get_engine_instance

log = get_logger(__name__)

FIELD_LABELS_FR = {
    "id_number": "numero_cin",
    "last_name": "nom",
    "first_name": "prenom",
    "birth_date": "date_naissance",
    "birth_place": "lieu_naissance",
}

INTERNAL_TO_API_FIELD = {
    "cin_number": "id_number",
    "family_name": "last_name",
    "first_name": "first_name",
    "date_of_birth": "birth_date",
    "place_of_birth": "birth_place",
}


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""

    return " ".join(str(text).replace("\r", "\n").split())


def to_ascii_digits(text: str) -> str:
    if not text:
        return ""

    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    eastern_digits = "۰۱۲۳۴۵۶۷۸۹"

    out = []

    for ch in text:
        if ch in arabic_digits:
            out.append(str(arabic_digits.index(ch)))
        elif ch in eastern_digits:
            out.append(str(eastern_digits.index(ch)))
        else:
            out.append(ch)

    return "".join(out)




def load_image(file_path: Union[str, Path]) -> np.ndarray:
    """
    Chargement robuste d'une image.

    Pourquoi ne pas utiliser seulement cv2.imread ?
    - cv2.imread peut échouer silencieusement sous Windows.
    - Certains chemins temporaires ou caractères spéciaux peuvent poser problème.
    - np.fromfile + cv2.imdecode est plus fiable.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    size = path.stat().st_size

    if size <= 0:
        raise ValueError(f"Image file is empty: {path}")

    data = np.fromfile(str(path), dtype=np.uint8)

    if data.size == 0:
        raise ValueError(f"Could not read image bytes: {path}")

    image = cv2.imdecode(data, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError(
            f"cv2.imdecode failed for: {path} "
            f"(size={size} bytes, suffix={path.suffix})"
        )

    return image


def resize_if_needed(
    image: np.ndarray,
    max_side: int = 1600,
) -> Tuple[np.ndarray, bool]:
    h, w = image.shape[:2]
    mx = max(h, w)

    if mx <= max_side:
        return image, False

    scale = max_side / mx
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    resized = cv2.resize(
        image,
        (new_w, new_h),
        interpolation=cv2.INTER_AREA,
    )

    return resized, True


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)

    l, a, b = cv2.split(lab)

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )

    l2 = clahe.apply(l)
    merged = cv2.merge([l2, a, b])

    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def simple_deskew(image: np.ndarray) -> Tuple[np.ndarray, bool]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    blur = cv2.GaussianBlur(
        gray,
        (3, 3),
        0,
    )

    _, th = cv2.threshold(
        blur,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    coords = np.column_stack(np.where(th < 255))

    if len(coords) < 200:
        return image, False

    rect = cv2.minAreaRect(coords.astype(np.float32))
    angle = rect[-1]

    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90

    if abs(angle) < 1.0 or abs(angle) > 12.0:
        return image, False

    h, w = image.shape[:2]
    center = (w // 2, h // 2)

    matrix = cv2.getRotationMatrix2D(
        center,
        angle,
        1.0,
    )

    rotated = cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return rotated, True


def compute_quality(image: np.ndarray) -> Dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray) / 255.0)
    contrast = float(np.std(gray) / 128.0)

    sharpness_n = min(sharpness / 500.0, 1.0)
    brightness_n = min(max(brightness, 0.0), 1.0)
    contrast_n = min(max(contrast, 0.0), 1.0)

    quality_score = round(
        (0.45 * sharpness_n)
        + (0.25 * brightness_n)
        + (0.30 * contrast_n),
        3,
    )

    return {
        "sharpness": round(sharpness_n, 3),
        "brightness": round(brightness_n, 3),
        "contrast": round(contrast_n, 3),
        "quality_score": quality_score,
    }


def field_to_dict(field: Any) -> Dict[str, Any]:
    if hasattr(field, "model_dump"):
        return field.model_dump(mode="json")

    if hasattr(field, "dict"):
        return field.dict()

    if isinstance(field, dict):
        return field

    return {
        "name": getattr(field, "name", None),
        "value": getattr(field, "value", None),
        "confidence": getattr(field, "confidence", 0.0),
        "validated": getattr(field, "validated", False),
        "raw_text": getattr(field, "raw_text", None),
        "error": getattr(field, "error", None),
        "selected_engine": getattr(field, "selected_engine", None),
        "selected_source": getattr(field, "selected_source", None),
        "review_required": getattr(field, "review_required", False),
        "reasons": getattr(field, "reasons", []),
    }


def get_engine_adapter(name: str):
    """Return an OCR engine through the central engine selector/factory path."""
    try:
        return get_engine_instance(name)

    except Exception:
        from app.engines.engine_factory import get_engine

        return get_engine(name)


def call_recognize_document(
    engine: Any,
    image: np.ndarray,
    language_hints: List[str],
) -> Tuple[str, float]:
    raw = None
    lang = language_hints[0] if language_hints else None

    if hasattr(engine, "recognize_document"):
        try:
            raw = engine.recognize_document(
                image=image,
                language_hints=language_hints,
                config={},
            )
        except Exception:
            raw = None

    if raw is None and hasattr(engine, "run"):
        try:
            raw = engine.run(
                image=image,
                language=lang,
            )
        except TypeError:
            raw = engine.run(
                image,
                lang,
            )
        except Exception:
            raw = None

    if raw is None:
        return "", 0.0

    if isinstance(raw, dict):
        text = (
            raw.get("full_text")
            or raw.get("text")
            or raw.get("raw_text")
            or ""
        )
        score = (
            raw.get("confidence")
            or raw.get("score")
            or raw.get("full_text_score")
            or 0.0
        )

        return normalize_text(text), safe_float(score, 0.0)

    text = (
        getattr(raw, "full_text", None)
        or getattr(raw, "text", None)
        or getattr(raw, "raw_text", "")
        or ""
    )

    score = (
        getattr(raw, "confidence", None)
        or getattr(raw, "score", None)
        or getattr(raw, "full_text_score", None)
        or 0.0
    )

    return normalize_text(text), safe_float(score, 0.0)


def first_non_empty_image(*candidates: Any) -> Optional[np.ndarray]:
    for item in candidates:
        if isinstance(item, np.ndarray):
            if item.size > 0:
                return item
        elif item is not None:
            return item

    return None


class CINFieldAdapter:
    def _digits_only(self, s: str) -> str:
        return re.sub(r"\D", "", to_ascii_digits(s or ""))

    def _extract_markers(self, raw_text: str) -> Dict[str, Any]:
        pats = {
            "cin_number": r"CIN_NUMBER:\s*(.+)",
            "family_name": r"CIN_FAMILY_NAME:\s*(.+)",
            "first_name": r"CIN_FIRST_NAME:\s*(.+)",
            "date_of_birth": r"CIN_DATE_OF_BIRTH:\s*(.+)",
            "place_of_birth": r"CIN_PLACE_OF_BIRTH:\s*(.+)",
        }

        out: Dict[str, Any] = {}

        for k, pat in pats.items():
            m = re.search(pat, raw_text or "")
            if m:
                out[k] = m.group(1).strip()

        return out

    def extract_fields(
        self,
        raw_text: str,
        *,
        trials: list | None = None,
    ) -> Dict[str, Any]:
        out = self._extract_markers(raw_text or "")

        try:
            from app.services.cin_rules import parse_date_any
        except Exception:
            parse_date_any = None

        cin = out.get("cin_number")

        if cin:
            d = self._digits_only(cin)
            if re.fullmatch(r"\d{8}", d):
                out["cin_number"] = d

        if out.get("date_of_birth") and parse_date_any:
            try:
                iso = parse_date_any(out["date_of_birth"])
                if iso:
                    out["date_of_birth"] = iso
            except Exception:
                pass

        return {
            k: v
            for k, v in out.items()
            if v not in (None, "", [], {})
        }

    def normalize_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        return dict(fields or {})


class PipelinePreprocessor:
    """Image preparation helper for the refactored pipeline."""

    def _layout_to_dict(self, layout: Any) -> Dict[str, Any]:
        if layout is None:
            return {}

        regions = []

        for region in getattr(layout, "regions", []) or []:
            regions.append(
                {
                    "type": getattr(region, "region_type", None),
                    "bbox": list(getattr(region, "bbox", []) or []),
                    "confidence": safe_float(
                        getattr(region, "confidence", 0.0),
                        0.0,
                    ),
                    "label": getattr(region, "label", None),
                }
            )

        return {
            "page_width": getattr(layout, "page_width", 0),
            "page_height": getattr(layout, "page_height", 0),
            "orientation": getattr(layout, "orientation", "unknown"),
            "has_table": bool(getattr(layout, "has_table", False)),
            "column_count": int(getattr(layout, "column_count", 1) or 1),
            "regions": regions[:50],
            "region_count": len(regions),
        }

    def prepare_for_cin(self, image: np.ndarray) -> Dict[str, Any]:
        """Prepare image for the CIN-specialized pipeline.

        This intentionally preserves the original app.zip behavior for CIN.

        Do not use the generic preprocess_for_pipeline() here. CIN ROI extraction
        is sensitive to resizing, deskewing, card localization and contrast.
        """

        resized, resized_flag = resize_if_needed(
            image,
            max_side=1600,
        )

        deskewed, deskew_flag = simple_deskew(resized)
        contrasted = enhance_contrast(deskewed)
        quality = compute_quality(contrasted)

        return {
            # Image brute uploadée, conservée pour les OCR ciblées/debug.
            "original": image,
            "source_image": image,
            "raw_image": image,

            # Images prétraitées utilisées par le pipeline CIN.
            "image": contrasted,
            "base_image": contrasted,
            "variants": {
                "base": contrasted,
                "contrast": contrasted,
            },
            "quality": quality,
            "transforms": {
                "resized": resized_flag,
                "deskewed": deskew_flag,
                "contrast": True,
                "mode": "cin_legacy_compatible",
            },
            "layout": {},
        }

    def prepare(self, image: np.ndarray) -> Dict[str, Any]:
        """Use the shared preprocessing + layout modules for generic extraction.

        CIN does not call this method. It uses prepare_for_cin() to preserve
        the original behavior.
        """

        try:
            bundle = preprocess_for_pipeline(image)

            variants = bundle.get("variants", {}) if isinstance(bundle, dict) else {}

            processed = first_non_empty_image(
                variants.get("contrast"),
                bundle.get("base"),
                image,
            )

            base = first_non_empty_image(
                bundle.get("base"),
                processed,
                image,
            )

            quality = bundle.get("quality") or compute_quality(processed)

            transforms = bundle.get("transforms") or {
                "resized": True,
                "deskewed": True,
                "contrast": True,
            }

        except Exception as exc:
            log.warning(
                "Shared preprocessing failed; using local fallback",
                extra={"error": str(exc)},
            )

            resized, resized_flag = resize_if_needed(
                image,
                max_side=1600,
            )

            deskewed, deskew_flag = simple_deskew(resized)
            processed = enhance_contrast(deskewed)
            base = processed
            variants = {"base": base}
            quality = compute_quality(processed)

            transforms = {
                "resized": resized_flag,
                "deskewed": deskew_flag,
                "contrast": True,
            }

        try:
            layout = get_layout_detector().detect(base)
            layout_info = self._layout_to_dict(layout)

        except Exception as exc:
            log.warning(
                "Layout analysis skipped",
                extra={"error": str(exc)},
            )
            layout_info = {}

        return {
            # Image brute uploadée, conservée pour l'OCR ciblée facture.
            # Important : ne pas la remplacer par l'image contrastée/deskewed.
            "original": image,
            "source_image": image,
            "raw_image": image,

            # Images prétraitées utilisées par l'OCR globale.
            "image": processed,
            "base_image": base,
            "variants": variants,
            "quality": quality,
            "transforms": transforms,
            "layout": layout_info,
        }
# Common utilities for the document processing pipeline.
"""from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path
from typing import Union

import cv2
import numpy as np

from app.core.logging import get_logger
from app.pipeline.io import load_file_as_pages
from app.pipeline.preprocess import preprocess_for_pipeline
from app.models.layout.layout_detector import get_layout_detector
from app.services.engine_selector import get_engine_instance

log = get_logger(__name__)

FIELD_LABELS_FR = {
    "id_number": "numero_cin",
    "last_name": "nom",
    "first_name": "prenom",
    "birth_date": "date_naissance",
    "birth_place": "lieu_naissance",
}

INTERNAL_TO_API_FIELD = {
    "cin_number": "id_number",
    "family_name": "last_name",
    "first_name": "first_name",
    "date_of_birth": "birth_date",
    "place_of_birth": "birth_place",
}


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""

    return " ".join(str(text).replace("\r", "\n").split())


def to_ascii_digits(text: str) -> str:
    if not text:
        return ""

    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    eastern_digits = "۰۱۲۳۴۵۶۷۸۹"

    out = []

    for ch in text:
        if ch in arabic_digits:
            out.append(str(arabic_digits.index(ch)))
        elif ch in eastern_digits:
            out.append(str(eastern_digits.index(ch)))
        else:
            out.append(ch)

    return "".join(out)




def load_image(file_path: Union[str, Path]) -> np.ndarray:
    
    Chargement robuste d'une image.

    Pourquoi ne pas utiliser seulement cv2.imread ?
    - cv2.imread peut échouer silencieusement sous Windows.
    - Certains chemins temporaires ou caractères spéciaux peuvent poser problème.
    - np.fromfile + cv2.imdecode est plus fiable.
    
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    size = path.stat().st_size

    if size <= 0:
        raise ValueError(f"Image file is empty: {path}")

    data = np.fromfile(str(path), dtype=np.uint8)

    if data.size == 0:
        raise ValueError(f"Could not read image bytes: {path}")

    image = cv2.imdecode(data, cv2.IMREAD_COLOR)

    if image is None:
        raise ValueError(
            f"cv2.imdecode failed for: {path} "
            f"(size={size} bytes, suffix={path.suffix})"
        )

    return image


def resize_if_needed(
    image: np.ndarray,
    max_side: int = 1600,
) -> Tuple[np.ndarray, bool]:
    h, w = image.shape[:2]
    mx = max(h, w)

    if mx <= max_side:
        return image, False

    scale = max_side / mx
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    resized = cv2.resize(
        image,
        (new_w, new_h),
        interpolation=cv2.INTER_AREA,
    )

    return resized, True


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)

    l, a, b = cv2.split(lab)

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )

    l2 = clahe.apply(l)
    merged = cv2.merge([l2, a, b])

    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def simple_deskew(image: np.ndarray) -> Tuple[np.ndarray, bool]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    blur = cv2.GaussianBlur(
        gray,
        (3, 3),
        0,
    )

    _, th = cv2.threshold(
        blur,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )

    coords = np.column_stack(np.where(th < 255))

    if len(coords) < 200:
        return image, False

    rect = cv2.minAreaRect(coords.astype(np.float32))
    angle = rect[-1]

    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90

    if abs(angle) < 1.0 or abs(angle) > 12.0:
        return image, False

    h, w = image.shape[:2]
    center = (w // 2, h // 2)

    matrix = cv2.getRotationMatrix2D(
        center,
        angle,
        1.0,
    )

    rotated = cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )

    return rotated, True


def compute_quality(image: np.ndarray) -> Dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray) / 255.0)
    contrast = float(np.std(gray) / 128.0)

    sharpness_n = min(sharpness / 500.0, 1.0)
    brightness_n = min(max(brightness, 0.0), 1.0)
    contrast_n = min(max(contrast, 0.0), 1.0)

    quality_score = round(
        (0.45 * sharpness_n)
        + (0.25 * brightness_n)
        + (0.30 * contrast_n),
        3,
    )

    return {
        "sharpness": round(sharpness_n, 3),
        "brightness": round(brightness_n, 3),
        "contrast": round(contrast_n, 3),
        "quality_score": quality_score,
    }


def field_to_dict(field: Any) -> Dict[str, Any]:
    if hasattr(field, "model_dump"):
        return field.model_dump(mode="json")

    if hasattr(field, "dict"):
        return field.dict()

    if isinstance(field, dict):
        return field

    return {
        "name": getattr(field, "name", None),
        "value": getattr(field, "value", None),
        "confidence": getattr(field, "confidence", 0.0),
        "validated": getattr(field, "validated", False),
        "raw_text": getattr(field, "raw_text", None),
        "error": getattr(field, "error", None),
        "selected_engine": getattr(field, "selected_engine", None),
        "selected_source": getattr(field, "selected_source", None),
        "review_required": getattr(field, "review_required", False),
        "reasons": getattr(field, "reasons", []),
    }


def get_engine_adapter(name: str):
    Return an OCR engine through the central engine selector/factory path.
    try:
        return get_engine_instance(name)

    except Exception:
        from app.engines.engine_factory import get_engine

        return get_engine(name)


def call_recognize_document(
    engine: Any,
    image: np.ndarray,
    language_hints: List[str],
) -> Tuple[str, float]:
    raw = None
    lang = language_hints[0] if language_hints else None

    if hasattr(engine, "recognize_document"):
        try:
            raw = engine.recognize_document(
                image=image,
                language_hints=language_hints,
                config={},
            )
        except Exception:
            raw = None

    if raw is None and hasattr(engine, "run"):
        try:
            raw = engine.run(
                image=image,
                language=lang,
            )
        except TypeError:
            raw = engine.run(
                image,
                lang,
            )
        except Exception:
            raw = None

    if raw is None:
        return "", 0.0

    if isinstance(raw, dict):
        text = (
            raw.get("full_text")
            or raw.get("text")
            or raw.get("raw_text")
            or ""
        )
        score = (
            raw.get("confidence")
            or raw.get("score")
            or raw.get("full_text_score")
            or 0.0
        )

        return normalize_text(text), safe_float(score, 0.0)

    text = (
        getattr(raw, "full_text", None)
        or getattr(raw, "text", None)
        or getattr(raw, "raw_text", "")
        or ""
    )

    score = (
        getattr(raw, "confidence", None)
        or getattr(raw, "score", None)
        or getattr(raw, "full_text_score", None)
        or 0.0
    )

    return normalize_text(text), safe_float(score, 0.0)


def first_non_empty_image(*candidates: Any) -> Optional[np.ndarray]:
    for item in candidates:
        if isinstance(item, np.ndarray):
            if item.size > 0:
                return item
        elif item is not None:
            return item

    return None


class CINFieldAdapter:
    def _digits_only(self, s: str) -> str:
        return re.sub(r"\D", "", to_ascii_digits(s or ""))

    def _extract_markers(self, raw_text: str) -> Dict[str, Any]:
        pats = {
            "cin_number": r"CIN_NUMBER:\s*(.+)",
            "family_name": r"CIN_FAMILY_NAME:\s*(.+)",
            "first_name": r"CIN_FIRST_NAME:\s*(.+)",
            "date_of_birth": r"CIN_DATE_OF_BIRTH:\s*(.+)",
            "place_of_birth": r"CIN_PLACE_OF_BIRTH:\s*(.+)",
        }

        out: Dict[str, Any] = {}

        for k, pat in pats.items():
            m = re.search(pat, raw_text or "")
            if m:
                out[k] = m.group(1).strip()

        return out

    def extract_fields(
        self,
        raw_text: str,
        *,
        trials: list | None = None,
    ) -> Dict[str, Any]:
        out = self._extract_markers(raw_text or "")

        try:
            from app.services.cin_rules import parse_date_any
        except Exception:
            parse_date_any = None

        cin = out.get("cin_number")

        if cin:
            d = self._digits_only(cin)
            if re.fullmatch(r"\d{8}", d):
                out["cin_number"] = d

        if out.get("date_of_birth") and parse_date_any:
            try:
                iso = parse_date_any(out["date_of_birth"])
                if iso:
                    out["date_of_birth"] = iso
            except Exception:
                pass

        return {
            k: v
            for k, v in out.items()
            if v not in (None, "", [], {})
        }

    def normalize_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        return dict(fields or {})


class PipelinePreprocessor:
    Image preparation helper for the refactored pipeline.

    def _layout_to_dict(self, layout: Any) -> Dict[str, Any]:
        if layout is None:
            return {}

        regions = []

        for region in getattr(layout, "regions", []) or []:
            regions.append(
                {
                    "type": getattr(region, "region_type", None),
                    "bbox": list(getattr(region, "bbox", []) or []),
                    "confidence": safe_float(
                        getattr(region, "confidence", 0.0),
                        0.0,
                    ),
                    "label": getattr(region, "label", None),
                }
            )

        return {
            "page_width": getattr(layout, "page_width", 0),
            "page_height": getattr(layout, "page_height", 0),
            "orientation": getattr(layout, "orientation", "unknown"),
            "has_table": bool(getattr(layout, "has_table", False)),
            "column_count": int(getattr(layout, "column_count", 1) or 1),
            "regions": regions[:50],
            "region_count": len(regions),
        }

    def prepare_for_cin(self, image: np.ndarray) -> Dict[str, Any]:
        Prepare image for the CIN-specialized pipeline.

        This intentionally preserves the original app.zip behavior for CIN.

        Do not use the generic preprocess_for_pipeline() here. CIN ROI extraction
        is sensitive to resizing, deskewing, card localization and contrast.
        

        resized, resized_flag = resize_if_needed(
            image,
            max_side=1600,
        )

        deskewed, deskew_flag = simple_deskew(resized)
        contrasted = enhance_contrast(deskewed)
        quality = compute_quality(contrasted)

        return {
            "image": contrasted,
            "base_image": contrasted,
            "variants": {
                "base": contrasted,
                "contrast": contrasted,
            },
            "quality": quality,
            "transforms": {
                "resized": resized_flag,
                "deskewed": deskew_flag,
                "contrast": True,
                "mode": "cin_legacy_compatible",
            },
            "layout": {},
        }

    def prepare(self, image: np.ndarray) -> Dict[str, Any]:
        Use the shared preprocessing + layout modules for generic extraction.

        CIN does not call this method. It uses prepare_for_cin() to preserve
        the original behavior.
        

        try:
            bundle = preprocess_for_pipeline(image)

            variants = bundle.get("variants", {}) if isinstance(bundle, dict) else {}

            processed = first_non_empty_image(
                variants.get("contrast"),
                bundle.get("base"),
                image,
            )

            base = first_non_empty_image(
                bundle.get("base"),
                processed,
                image,
            )

            quality = bundle.get("quality") or compute_quality(processed)

            transforms = bundle.get("transforms") or {
                "resized": True,
                "deskewed": True,
                "contrast": True,
            }

        except Exception as exc:
            log.warning(
                "Shared preprocessing failed; using local fallback",
                extra={"error": str(exc)},
            )

            resized, resized_flag = resize_if_needed(
                image,
                max_side=1600,
            )

            deskewed, deskew_flag = simple_deskew(resized)
            processed = enhance_contrast(deskewed)
            base = processed
            variants = {"base": base}
            quality = compute_quality(processed)

            transforms = {
                "resized": resized_flag,
                "deskewed": deskew_flag,
                "contrast": True,
            }

        try:
            layout = get_layout_detector().detect(base)
            layout_info = self._layout_to_dict(layout)

        except Exception as exc:
            log.warning(
                "Layout analysis skipped",
                extra={"error": str(exc)},
            )
            layout_info = {}

        return {
            "image": processed,
            "base_image": base,
            "variants": variants,
            "quality": quality,
            "transforms": transforms,
            "layout": layout_info,
        }"""