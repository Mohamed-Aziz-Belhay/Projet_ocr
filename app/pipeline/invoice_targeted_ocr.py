"""
app/pipeline/invoice_targeted_ocr.py

OCR ciblée pour les factures tunisiennes.

Objectif :
- compléter l'OCR pleine page uniquement pour les champs d'en-tête difficiles ;
- récupérer invoice_number et invoice_date quand l'OCR pleine page lit mal le haut de facture ;
- ne jamais utiliser l'OCR ciblée pour les montants ;
- ne jamais fusionner l'OCR ciblée dans le raw_text principal ;
- utiliser EasyOCR seulement comme fallback en mode full/debug, si un moteur EasyOCR est fourni.

Important :
- Ce module retourne des diagnostics et des candidats structurés.
- Le merge avec le résultat final doit être fait ailleurs, champ par champ.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import time
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


RecognizeFn = Callable[[Any, np.ndarray, Optional[Sequence[str]]], Tuple[str, float]]
NormalizeFn = Callable[[str], str]


# =============================================================================
# Dataclasses
# =============================================================================

@dataclass
class InvoiceOCRZone:
    zone_id: str
    label: str
    bbox_norm: Tuple[float, float, float, float]
    scale: float = 2.0
    purpose: str = "generic"


@dataclass
class InvoiceOCRZoneResult:
    zone_id: str
    label: str
    purpose: str
    bbox_norm: Tuple[float, float, float, float]
    image_shape: Tuple[int, int]
    crop_shape: Tuple[int, int]
    scale: float
    variant_id: str
    engine_name: str
    text: str
    score: float
    extracted_invoice_number: Optional[str] = None
    extracted_invoice_date: Optional[str] = None
    error: Optional[str] = None


@dataclass
class TargetedCandidate:
    field_name: str
    value: str
    raw: str
    zone_id: str
    variant_id: str
    engine_name: str
    score: float
    confidence: float


# =============================================================================
# Zones OCR
# =============================================================================

# Anciennes zones larges : gardées comme fallback, mais moins prioritaires.
DEFAULT_INVOICE_ZONES: List[InvoiceOCRZone] = [
    InvoiceOCRZone(
        zone_id="top_left_invoice_number_date",
        label="Zone haut gauche : numéro et date facture",
        bbox_norm=(0.00, 0.00, 0.42, 0.24),
        scale=3.0,
        purpose="header",
    ),
    InvoiceOCRZone(
        zone_id="top_center_reference_period",
        label="Zone haut centre : période et référence unique",
        bbox_norm=(0.25, 0.00, 0.86, 0.26),
        scale=2.5,
        purpose="header",
    ),
    InvoiceOCRZone(
        zone_id="upper_middle_client_block",
        label="Zone client et date limite",
        bbox_norm=(0.12, 0.16, 0.90, 0.45),
        scale=2.0,
        purpose="client_block",
    ),
    InvoiceOCRZone(
        zone_id="full_header",
        label="En-tête complet de facture",
        bbox_norm=(0.00, 0.00, 1.00, 0.32),
        scale=2.0,
        purpose="header",
    ),
]


# Zones strictes TTN.
# Elles ciblent seulement l'en-tête gauche où se trouvent souvent :
# - Facture N°
# - Date
#
# Pour une image 800x1202 :
# ttn_header_left_strict ≈ x 24->288, y 90->204.
TTN_STRICT_HEADER_ZONES: List[InvoiceOCRZone] = [
    InvoiceOCRZone(
        zone_id="ttn_header_left_strict",
        label="TTN haut gauche strict : numéro facture et date",
        bbox_norm=(0.03, 0.075, 0.36, 0.17),
        scale=4.0,
        purpose="invoice_number_date",
    ),
    InvoiceOCRZone(
        zone_id="ttn_header_left_medium",
        label="TTN haut gauche moyen : numéro facture et date",
        bbox_norm=(0.02, 0.060, 0.42, 0.20),
        scale=3.8,
        purpose="invoice_number_date",
    ),
    InvoiceOCRZone(
        zone_id="ttn_invoice_number_line",
        label="TTN ligne numéro facture",
        bbox_norm=(0.03, 0.075, 0.30, 0.125),
        scale=4.0,
        purpose="invoice_number",
    ),
    InvoiceOCRZone(
        zone_id="ttn_invoice_date_line",
        label="TTN ligne date facture",
        bbox_norm=(0.03, 0.115, 0.30, 0.175),
        scale=4.0,
        purpose="invoice_date",
    ),
    InvoiceOCRZone(
        zone_id="ttn_header_left_wide",
        label="TTN haut gauche large fallback",
        bbox_norm=(0.00, 0.045, 0.48, 0.24),
        scale=3.2,
        purpose="invoice_number_date",
    ),
    InvoiceOCRZone(
        zone_id="ttn_header_full_top",
        label="TTN en-tête complet haut",
        bbox_norm=(0.00, 0.035, 1.00, 0.20),
        scale=2.6,
        purpose="header",
    ),
]


# =============================================================================
# Helpers image
# =============================================================================

def _ensure_numpy_image(image: Any) -> np.ndarray:
    if image is None:
        raise ValueError("image is None")

    if isinstance(image, np.ndarray):
        return image

    # Support PIL Image si nécessaire.
    if hasattr(image, "convert"):
        return np.array(image.convert("RGB"))

    raise TypeError(f"Unsupported image type: {type(image)!r}")


def _crop_norm(image: np.ndarray, bbox_norm: Tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x1n, y1n, x2n, y2n = bbox_norm

    x1 = max(0, min(w - 1, int(round(x1n * w))))
    y1 = max(0, min(h - 1, int(round(y1n * h))))
    x2 = max(1, min(w, int(round(x2n * w))))
    y2 = max(1, min(h, int(round(y2n * h))))

    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid crop bbox: {bbox_norm}")

    return image[y1:y2, x1:x2].copy()


def _to_bgr_if_needed(image: np.ndarray) -> np.ndarray:
    """
    Essaie de garder un format compatible OpenCV.

    Si l'image vient de PIL, elle peut être RGB.
    Si elle vient de cv2.imread, elle est BGR.
    Ici on ne force pas RGB/BGR pour OCR, on garde seulement 3 canaux.
    """
    if image.ndim == 2:
        if cv2 is None:
            return np.stack([image, image, image], axis=-1)
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

    if image.ndim == 3 and image.shape[2] == 4:
        if cv2 is None:
            return image[:, :, :3]
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    return image


def _resize_for_ocr(crop: np.ndarray, scale: float = 2.0, min_width: int = 900) -> np.ndarray:
    if crop is None or crop.size == 0:
        raise ValueError("empty crop")

    h, w = crop.shape[:2]

    if w <= 0 or h <= 0:
        raise ValueError("invalid crop shape")

    computed_scale = max(scale, min_width / max(1, w))
    computed_scale = min(computed_scale, 4.5)

    new_w = max(1, int(round(w * computed_scale)))
    new_h = max(1, int(round(h * computed_scale)))

    if cv2 is None:
        y_idx = np.linspace(0, h - 1, new_h).astype(int)
        x_idx = np.linspace(0, w - 1, new_w).astype(int)
        return crop[y_idx][:, x_idx]

    return cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)


def _adaptive_scale_for_header(image_shape: Tuple[int, int], base_scale: float) -> float:
    """
    Adapte l'agrandissement selon la taille de page.

    Cas observé :
    - 800x1202 : petits textes, besoin de scale élevé.
    - 1240x1754 : texte plus lisible, scale moins agressif.
    """
    h, w = image_shape[:2]

    if w < 900:
        return max(base_scale, 4.0)

    if w < 1100:
        return max(base_scale, 3.5)

    return base_scale


def _prepare_header_crop_for_ocr(crop: np.ndarray, scale: float) -> List[Tuple[str, np.ndarray]]:
    """
    Prétraitement spécialisé pour l'en-tête facture.

    Retourne plusieurs variantes parce que PaddleOCR/EasyOCR ne réagissent pas
    toujours pareil :
    - couleur agrandie ;
    - gris + CLAHE ;
    - sharpen léger ;
    - threshold doux.

    On évite un threshold agressif par défaut, car les textes d'en-tête TTN sont fins.
    """
    crop = _to_bgr_if_needed(crop)
    upscaled = _resize_for_ocr(crop, scale=scale, min_width=900)

    variants: List[Tuple[str, np.ndarray]] = [("color_upscaled", upscaled)]

    if cv2 is None:
        return variants

    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)

    # CLAHE léger : améliore le contraste local sans tuer les caractères fins.
    clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
    gray_clahe = clahe.apply(gray)

    gray_clahe_bgr = cv2.cvtColor(gray_clahe, cv2.COLOR_GRAY2BGR)
    variants.append(("gray_clahe", gray_clahe_bgr))

    # Sharpen léger.
    blur = cv2.GaussianBlur(gray_clahe, (0, 0), 1.0)
    sharp = cv2.addWeighted(gray_clahe, 1.55, blur, -0.55, 0)
    sharp_bgr = cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)
    variants.append(("gray_clahe_sharp", sharp_bgr))

    # Threshold doux, gardé comme variante fallback.
    # Pas trop agressif pour éviter de perdre "Facture N°" / "Date".
    try:
        soft = cv2.adaptiveThreshold(
            sharp,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            9,
        )
        soft_bgr = cv2.cvtColor(soft, cv2.COLOR_GRAY2BGR)
        variants.append(("soft_threshold", soft_bgr))
    except Exception:
        pass

    return variants


def _safe_filename(value: str) -> str:
    """
    Nettoie un nom pour l'utiliser comme nom de fichier.
    """
    value = str(value or "").strip()
    value = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "crop"


def _save_debug_crop(
    image: np.ndarray,
    debug_dir: Optional[str],
    zone_id: str,
    variant_id: str,
    engine_name: str,
    index: int,
) -> Optional[str]:
    """
    Sauvegarde un crop/variant OCR pour debug visuel.

    Retourne le chemin sauvegardé ou None.
    """
    if not debug_dir:
        return None

    if cv2 is None:
        return None

    try:
        out_dir = Path(debug_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        safe_zone = _safe_filename(zone_id)
        safe_variant = _safe_filename(variant_id)
        safe_engine = _safe_filename(engine_name)

        filename = f"{index:03d}_{safe_zone}_{safe_variant}_{safe_engine}.jpg"
        path = out_dir / filename

        img = image

        if img is None or getattr(img, "size", 0) == 0:
            return None

        # Si grayscale, convertir en BGR pour sauvegarde homogène.
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        # Compression JPEG correcte pour debug.
        cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

        return str(path)

    except Exception:
        return None


# =============================================================================
# Helpers texte / extraction header
# =============================================================================

def _normalize_ocr_text(text: str) -> str:
    text = text or ""
    text = text.replace("\u00a0", " ")
    text = text.replace("|", " ")
    text = text.replace("’", "'")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_date_to_iso(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    raw = str(raw).strip()
    parts = re.split(r"[/\-\.]", raw)

    if len(parts) != 3:
        return raw

    a, b, y = parts

    try:
        first = int(a)
        second = int(b)

        if len(y) == 2:
            year_2 = int(y)
            year = 2000 + year_2 if year_2 < 50 else 1900 + year_2
        else:
            year = int(y)

        # Tunisie : jj/mm/aaaa par défaut.
        day = first
        month = second

        # Si mois invalide mais jour possible en format US, on inverse.
        if month > 12 and 1 <= day <= 12:
            day, month = month, day

        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
            return raw

        return f"{year:04d}-{month:02d}-{day:02d}"

    except Exception:
        return raw


def _valid_invoice_number(value: Optional[str]) -> bool:
    if not value:
        return False

    value = str(value).strip()

    if not re.search(r"\d", value):
        return False

    if len(value) > 20:
        return False

    bad = {
        "date",
        "total",
        "tva",
        "ttc",
        "ht",
        "client",
        "facture",
        "periode",
        "période",
        "reference",
        "référence",
    }

    if value.lower() in bad:
        return False

    # Éviter de prendre une référence unique ou un montant.
    if re.fullmatch(r"\d{12,}", value):
        return False

    if re.fullmatch(r"\d+[.,]\d+", value):
        return False

    return True


def _extract_invoice_number_from_header(text: str) -> Tuple[Optional[str], Optional[str]]:
    compact = _compact_text(text)

    patterns = [
        re.compile(
            r"(?:Facture\s*N[°o]?\s*[:#\-]?\s*)"
            r"([A-Z0-9][A-Z0-9\-\/]{0,20})",
            re.I,
        ),
        re.compile(
            r"(?:Facture\s+num[eé]ro\s*[:#\-]?\s*)"
            r"([A-Z0-9][A-Z0-9\-\/]{0,20})",
            re.I,
        ),
        re.compile(r"\bFacture\s*[:#\-]?\s*([0-9][A-Z0-9\-\/]{0,20})", re.I),
        re.compile(r"\bN[°o]\s*[:#\-]?\s*([0-9]{1,10})\b", re.I),
    ]

    for pattern in patterns:
        match = pattern.search(compact)
        if not match:
            continue

        value = match.group(1).strip()

        if _valid_invoice_number(value):
            return value, match.group(0).strip()

    return None, None


def _extract_invoice_date_from_header(text: str) -> Tuple[Optional[str], Optional[str]]:
    compact = _compact_text(text)

    patterns = [
        re.compile(
            r"\bDate\s*[:#\-]?\s*"
            r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
            re.I,
        ),
        re.compile(
            r"\bDate\s+facture\s*[:#\-]?\s*"
            r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
            re.I,
        ),
        re.compile(r"\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4})\b"),
    ]

    for pattern in patterns:
        for match in pattern.finditer(compact):
            raw = match.group(1).strip() if match.lastindex else match.group(0).strip()
            around = compact[max(0, match.start() - 40): match.end() + 40].lower()

            # Important : ne pas confondre avec Date Limite de paiement.
            if "limite" in around or "paiement" in around or "echeance" in around or "échéance" in around:
                continue

            return _parse_date_to_iso(raw), match.group(0).strip()

    return None, None


def _candidate_confidence(
    field_name: str,
    value: str,
    score: float,
    zone: InvoiceOCRZone,
    engine_name: str,
) -> float:
    """
    Score métier simple.
    """
    base = float(score or 0.0)

    # Certains moteurs retournent 0 même si du texte existe.
    if base <= 0:
        base = 0.55

    if zone.purpose in {"invoice_number", "invoice_date", "invoice_number_date"}:
        base += 0.12

    if zone.zone_id.endswith("_strict"):
        base += 0.06

    if engine_name.lower().startswith("easy"):
        base -= 0.02  # fallback utile, mais on le garde légèrement moins prioritaire.

    if field_name == "invoice_date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        base += 0.08

    if field_name == "invoice_number" and _valid_invoice_number(value):
        base += 0.08

    return max(0.0, min(0.98, base))


def _extract_header_candidates(
    text: str,
    zone: InvoiceOCRZone,
    variant_id: str,
    engine_name: str,
    score: float,
) -> List[TargetedCandidate]:
    candidates: List[TargetedCandidate] = []

    invoice_number, invoice_number_raw = _extract_invoice_number_from_header(text)

    if invoice_number:
        candidates.append(
            TargetedCandidate(
                field_name="invoice_number",
                value=invoice_number,
                raw=invoice_number_raw or invoice_number,
                zone_id=zone.zone_id,
                variant_id=variant_id,
                engine_name=engine_name,
                score=float(score or 0.0),
                confidence=_candidate_confidence(
                    "invoice_number",
                    invoice_number,
                    float(score or 0.0),
                    zone,
                    engine_name,
                ),
            )
        )

    invoice_date, invoice_date_raw = _extract_invoice_date_from_header(text)

    if invoice_date:
        candidates.append(
            TargetedCandidate(
                field_name="invoice_date",
                value=invoice_date,
                raw=invoice_date_raw or invoice_date,
                zone_id=zone.zone_id,
                variant_id=variant_id,
                engine_name=engine_name,
                score=float(score or 0.0),
                confidence=_candidate_confidence(
                    "invoice_date",
                    invoice_date,
                    float(score or 0.0),
                    zone,
                    engine_name,
                ),
            )
        )

    return candidates


def _best_candidate(candidates: List[TargetedCandidate], field_name: str) -> Optional[TargetedCandidate]:
    relevant = [c for c in candidates if c.field_name == field_name and c.value]

    if not relevant:
        return None

    relevant.sort(key=lambda c: c.confidence, reverse=True)
    return relevant[0]


def _build_orientation_variants(image: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """
    Construit les variantes d'orientation pour l'OCR ciblée header.

    Pourquoi :
    - certains preprocessors peuvent donner une image tournée ;
    - les crops debug montrent une orientation à 90° ;
    - on teste donc normal, rot90cw, rot90ccw et rot180.

    Ces variantes ne modifient pas le résultat global.
    Elles servent uniquement à trouver invoice_number / invoice_date.
    """
    variants: List[Tuple[str, np.ndarray]] = [("normal", image)]

    if cv2 is not None:
        try:
            variants.append(("rot90cw", cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)))
        except Exception:
            pass

        try:
            variants.append(("rot90ccw", cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)))
        except Exception:
            pass

        try:
            variants.append(("rot180", cv2.rotate(image, cv2.ROTATE_180)))
        except Exception:
            pass

    return variants


# =============================================================================
# Public API
# =============================================================================

def build_invoice_targeted_zones(
    image_shape: Tuple[int, int],
    profile: Optional[str] = None,
    strict_ttn: bool = True,
) -> List[InvoiceOCRZone]:
    """
    Construit les zones selon le type de facture et la dimension.

    Pour TTN, on priorise les zones strictes.
    """
    zones: List[InvoiceOCRZone] = []

    if strict_ttn:
        zones.extend(TTN_STRICT_HEADER_ZONES)

    zones.extend(DEFAULT_INVOICE_ZONES)

    image_h, image_w = image_shape[:2]
    adapted: List[InvoiceOCRZone] = []

    for z in zones:
        scale = z.scale

        if z.purpose in {"invoice_number", "invoice_date", "invoice_number_date", "header"}:
            scale = _adaptive_scale_for_header((image_h, image_w), z.scale)

        adapted.append(
            InvoiceOCRZone(
                zone_id=z.zone_id,
                label=z.label,
                bbox_norm=z.bbox_norm,
                scale=scale,
                purpose=z.purpose,
            )
        )

    return adapted

def run_invoice_targeted_ocr(
    image: Any,
    engine: Any,
    language_hints: Optional[Sequence[str]],
    recognize_fn: RecognizeFn,
    normalize_fn: Optional[NormalizeFn] = None,
    zones: Optional[List[InvoiceOCRZone]] = None,
    *,
    # Nouveaux paramètres optionnels.
    mode: str = "full",
    missing_fields: Optional[Sequence[str]] = None,
    easyocr_engine: Any = None,
    easyocr_recognize_fn: Optional[RecognizeFn] = None,
    use_easyocr_fallback: bool = True,
    strict_ttn_zones: bool = True,
    save_debug_crops: bool = False,
    debug_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Exécute l'OCR ciblée facture.

    Paramètres principaux :
    - engine : moteur principal, généralement PaddleOCR.
    - easyocr_engine : moteur EasyOCR optionnel.
    - easyocr_recognize_fn : fonction OCR EasyOCR optionnelle.
    - mode : "balanced", "full" ou "debug".
    - missing_fields : liste des champs manquants avant OCR ciblée.

    Retour :
    {
      "executed": bool,
      "combined_text": str,
      "zones": [...],
      "candidates": [...],
      "best_candidates": {
          "invoice_number": {...} | None,
          "invoice_date": {...} | None
      }
    }
    """
    normalize_fn = normalize_fn or _normalize_ocr_text
    missing_fields = list(missing_fields or [])

    # Sécurité : pas d'OCR ciblée en balanced.
    if mode not in {"full", "debug"}:
        return {
            "executed": False,
            "reason": "disabled_outside_supported_modes",
            "mode": mode,
            "combined_text": "",
            "zones": [],
            "candidates": [],
            "best_candidates": {
                "invoice_number": None,
                "invoice_date": None,
            },
        }

    # Sécurité : l'OCR ciblée ne sert qu'à ces champs.
    allowed_missing = {"invoice_number", "invoice_date"}
    requested_missing = [f for f in missing_fields if f in allowed_missing]

    if missing_fields and not requested_missing:
        return {
            "executed": False,
            "reason": "no_allowed_missing_header_fields",
            "mode": mode,
            "missing_fields": missing_fields,
            "combined_text": "",
            "zones": [],
            "candidates": [],
            "best_candidates": {
                "invoice_number": None,
                "invoice_date": None,
            },
        }

    try:
        np_image = _ensure_numpy_image(image)
    except Exception as exc:
        return {
            "executed": False,
            "reason": "invalid_image",
            "combined_text": "",
            "zones": [],
            "zone_count": 0,
            "non_empty_zone_count": 0,
            "candidates": [],
            "best_candidates": {
                "invoice_number": None,
                "invoice_date": None,
            },
            "error": str(exc),
        }

    image_h, image_w = np_image.shape[:2]

    if zones is None:
        zones = build_invoice_targeted_zones(
            image_shape=(image_h, image_w),
            strict_ttn=strict_ttn_zones,
        )

    # Production performance policy:
    # In balanced/full, use only the four TTN header zones that proved useful.
    # Keep the order explicit so PaddleOCR reaches the likely matches first.
    # In debug/diagnostic, keep all zones for investigation.
    if mode in {"balanced", "full"}:
        fast_zone_order = [
            "ttn_header_left_strict",
            "ttn_header_left_medium",
            "ttn_very_top_left_invoice_number",
            "ttn_very_top_left_date",
        ]
        by_zone_id = {z.zone_id: z for z in zones}
        zones = [by_zone_id[zid] for zid in fast_zone_order if zid in by_zone_id]

    results: List[InvoiceOCRZoneResult] = []
    combined_parts: List[str] = []
    all_candidates: List[TargetedCandidate] = []

    debug_saved_crops: List[Dict[str, Any]] = []
    debug_crop_index = 0

    if save_debug_crops and not debug_dir:
        ts = time.strftime("%Y%m%d_%H%M%S")
        debug_dir = f"debug/invoice_crops/{ts}"

    # Performance policy:
    # - balanced/full: normal orientation only.
    # - debug/diagnostic: test all orientations for visual diagnosis.
    if mode in {"debug", "diagnostic"}:
        orientation_variants = _build_orientation_variants(np_image)
    else:
        orientation_variants = [("normal", np_image)]

    stop_early = False
    early_stop_debug: Dict[str, Any] = {
        "enabled": mode in {"balanced", "full"},
        "triggered": False,
        "reason": None,
        "requested_fields": requested_missing,
        "found_fields": [],
    }

    def _targeted_requested_fields_found() -> bool:
        if mode not in {"balanced", "full"}:
            return False

        wanted = set(requested_missing or ["invoice_number", "invoice_date"])
        found = {
            c.field_name
            for c in all_candidates
            if c.field_name in wanted
            and c.value not in (None, "", [])
            and float(c.confidence or 0.0) >= 0.60
        }

        early_stop_debug["found_fields"] = sorted(found)
        return bool(wanted) and wanted.issubset(found)

    for orientation_id, oriented_image in orientation_variants:
        oriented_h, oriented_w = oriented_image.shape[:2]

        for zone in zones:
            try:
                crop = _crop_norm(oriented_image, zone.bbox_norm)
                variants = _prepare_header_crop_for_ocr(crop, scale=zone.scale)

                # In production modes, avoid testing all preprocessing variants
                # once a clean color/CLAHE read is enough.
                # This keeps debug/diagnostic exhaustive.
                if mode in {"balanced", "full"}:
                    variants = [
                        item
                        for item in variants
                        if item[0] in {"color_upscaled", "gray_clahe"}
                    ]

                for variant_id, prepared in variants:
                    full_variant_id = f"{orientation_id}_{variant_id}"
                    debug_zone_id = f"{orientation_id}_{zone.zone_id}"

                    debug_path_primary = None

                    if save_debug_crops:
                        debug_crop_index += 1
                        debug_path_primary = _save_debug_crop(
                            image=prepared,
                            debug_dir=debug_dir,
                            zone_id=debug_zone_id,
                            variant_id=variant_id,
                            engine_name="primary",
                            index=debug_crop_index,
                        )

                        if debug_path_primary:
                            debug_saved_crops.append(
                                {
                                    "path": debug_path_primary,
                                    "orientation_id": orientation_id,
                                    "zone_id": zone.zone_id,
                                    "debug_zone_id": debug_zone_id,
                                    "variant_id": full_variant_id,
                                    "engine_name": "primary",
                                    "bbox_norm": zone.bbox_norm,
                                    "crop_shape": prepared.shape[:2],
                                }
                            )

                    # 1) OCR moteur principal.
                    text, score = recognize_fn(engine, prepared, language_hints)
                    text = normalize_fn(text or "").strip()

                    candidates = _extract_header_candidates(
                        text=text,
                        zone=zone,
                        variant_id=full_variant_id,
                        engine_name="primary",
                        score=float(score or 0.0),
                    )

                    all_candidates.extend(candidates)

                    invoice_number = next(
                        (
                            c.value
                            for c in candidates
                            if c.field_name == "invoice_number"
                        ),
                        None,
                    )
                    invoice_date = next(
                        (
                            c.value
                            for c in candidates
                            if c.field_name == "invoice_date"
                        ),
                        None,
                    )

                    results.append(
                        InvoiceOCRZoneResult(
                            zone_id=zone.zone_id,
                            label=zone.label,
                            purpose=zone.purpose,
                            bbox_norm=zone.bbox_norm,
                            image_shape=(oriented_h, oriented_w),
                            crop_shape=prepared.shape[:2],
                            scale=zone.scale,
                            variant_id=full_variant_id,
                            engine_name="primary",
                            text=text,
                            score=float(score or 0.0),
                            extracted_invoice_number=invoice_number,
                            extracted_invoice_date=invoice_date,
                            error=None,
                        )
                    )

                    if text:
                        combined_parts.append(
                            f"[primary:{orientation_id}:{zone.zone_id}:{variant_id}] {text}"
                        )

                    if _targeted_requested_fields_found():
                        stop_early = True
                        early_stop_debug["triggered"] = True
                        early_stop_debug["reason"] = "requested_header_fields_found_by_primary_ocr"
                        break

                    # 2) EasyOCR fallback seulement en debug/diagnostic.
                    # En balanced/full, EasyOCR est désactivé pour garder le temps stable.
                    needs_easyocr = (
                        use_easyocr_fallback
                        and easyocr_engine is not None
                        and easyocr_recognize_fn is not None
                        and mode in {"debug", "diagnostic"}
                    )

                    if needs_easyocr:
                        found_number = any(
                            c.field_name == "invoice_number" for c in candidates
                        )
                        found_date = any(
                            c.field_name == "invoice_date" for c in candidates
                        )

                        if requested_missing:
                            useful = (
                                (
                                    "invoice_number" in requested_missing
                                    and not found_number
                                )
                                or (
                                    "invoice_date" in requested_missing
                                    and not found_date
                                )
                            )
                        else:
                            useful = not (found_number and found_date)

                        if useful:
                            try:
                                if save_debug_crops:
                                    debug_crop_index += 1
                                    debug_path_easy = _save_debug_crop(
                                        image=prepared,
                                        debug_dir=debug_dir,
                                        zone_id=debug_zone_id,
                                        variant_id=variant_id,
                                        engine_name="easyocr",
                                        index=debug_crop_index,
                                    )

                                    if debug_path_easy:
                                        debug_saved_crops.append(
                                            {
                                                "path": debug_path_easy,
                                                "orientation_id": orientation_id,
                                                "zone_id": zone.zone_id,
                                                "debug_zone_id": debug_zone_id,
                                                "variant_id": full_variant_id,
                                                "engine_name": "easyocr",
                                                "bbox_norm": zone.bbox_norm,
                                                "crop_shape": prepared.shape[:2],
                                            }
                                        )

                                easy_text, easy_score = easyocr_recognize_fn(
                                    easyocr_engine,
                                    prepared,
                                    language_hints,
                                )
                                easy_text = normalize_fn(easy_text or "").strip()

                                easy_candidates = _extract_header_candidates(
                                    text=easy_text,
                                    zone=zone,
                                    variant_id=full_variant_id,
                                    engine_name="easyocr",
                                    score=float(easy_score or 0.0),
                                )

                                all_candidates.extend(easy_candidates)

                                easy_invoice_number = next(
                                    (
                                        c.value
                                        for c in easy_candidates
                                        if c.field_name == "invoice_number"
                                    ),
                                    None,
                                )
                                easy_invoice_date = next(
                                    (
                                        c.value
                                        for c in easy_candidates
                                        if c.field_name == "invoice_date"
                                    ),
                                    None,
                                )

                                results.append(
                                    InvoiceOCRZoneResult(
                                        zone_id=zone.zone_id,
                                        label=zone.label,
                                        purpose=zone.purpose,
                                        bbox_norm=zone.bbox_norm,
                                        image_shape=(oriented_h, oriented_w),
                                        crop_shape=prepared.shape[:2],
                                        scale=zone.scale,
                                        variant_id=full_variant_id,
                                        engine_name="easyocr",
                                        text=easy_text,
                                        score=float(easy_score or 0.0),
                                        extracted_invoice_number=easy_invoice_number,
                                        extracted_invoice_date=easy_invoice_date,
                                        error=None,
                                    )
                                )

                                if easy_text:
                                    combined_parts.append(
                                        f"[easyocr:{orientation_id}:{zone.zone_id}:{variant_id}] {easy_text}"
                                    )

                            except Exception as exc:
                                results.append(
                                    InvoiceOCRZoneResult(
                                        zone_id=zone.zone_id,
                                        label=zone.label,
                                        purpose=zone.purpose,
                                        bbox_norm=zone.bbox_norm,
                                        image_shape=(oriented_h, oriented_w),
                                        crop_shape=prepared.shape[:2],
                                        scale=zone.scale,
                                        variant_id=full_variant_id,
                                        engine_name="easyocr",
                                        text="",
                                        score=0.0,
                                        extracted_invoice_number=None,
                                        extracted_invoice_date=None,
                                        error=f"{type(exc).__name__}: {exc}",
                                    )
                                )

                if stop_early:
                    break

            except Exception as exc:
                results.append(
                    InvoiceOCRZoneResult(
                        zone_id=zone.zone_id,
                        label=zone.label,
                        purpose=zone.purpose,
                        bbox_norm=zone.bbox_norm,
                        image_shape=(oriented_h, oriented_w),
                        crop_shape=(0, 0),
                        scale=zone.scale,
                        variant_id=f"{orientation_id}_none",
                        engine_name="primary",
                        text="",
                        score=0.0,
                        extracted_invoice_number=None,
                        extracted_invoice_date=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )

            if stop_early:
                break

        if stop_early:
            break

    best_invoice_number = _best_candidate(all_candidates, "invoice_number")
    best_invoice_date = _best_candidate(all_candidates, "invoice_date")

    combined_text = "\n".join(combined_parts).strip()

    return {
        "executed": True,
        "mode": mode,
        "image_shape": {
            "height": image_h,
            "width": image_w,
        },
        "combined_text": combined_text,
        "zones": [asdict(r) for r in results],
        "zone_count": len(results),
        "non_empty_zone_count": sum(1 for r in results if r.text.strip()),
        "candidates": [asdict(c) for c in all_candidates],
        "best_candidates": {
            "invoice_number": asdict(best_invoice_number)
            if best_invoice_number
            else None,
            "invoice_date": asdict(best_invoice_date)
            if best_invoice_date
            else None,
        },
        "debug_saved_crops": debug_saved_crops,
        "debug_dir": debug_dir if save_debug_crops else None,
        "early_stop": early_stop_debug,
        "note": (
            "combined_text is diagnostic only. Do not merge it into raw_text principal. "
            "Use best_candidates only for invoice_number and invoice_date."
        ),
    }