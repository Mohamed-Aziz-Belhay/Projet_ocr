"""
app/pipeline/cin_runner.py

Specialized pipeline runner for Tunisian CIN extraction.

Modes:
- fast:
    easyocr_boxes first
    paddle_boxes only if critical fields are missing
    no targeted ROI OCR / no full OCR / no Tesseract

- balanced:
    spatial-first, targeted ROI fallback, optional full fallback

- full:
    exhaustive fallback (Tesseract only if CIN_FULL_INCLUDE_TESSERACT=True)

Fix log v2.1:
  • _run_spatial_boxes now stores raw box text on the trial (was always "").
    This gives _apply_cin_postprocess the Arabic label context it needs.
  • _can_return_early now uses the POST-postprocess validated state, so
    corrected fields stop the pipeline early (speed fix).
  • family_name = ALL words on the اللقب line (not just the first token).
  • first_name  = words on الاسم line BEFORE the first بن / بنت only.
  • Reversed-word-order RTL fix tried automatically when names are missing.
  • بنت handled as an explicit first_name stop (feminine cards).
"""
from __future__ import annotations

import time
import re
from typing import Any, Dict, List, Optional, Tuple
from datetime import date

import numpy as np

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.pipeline.common import (
    CINFieldAdapter,
    FIELD_LABELS_FR,
    INTERNAL_TO_API_FIELD,
    call_recognize_document,
    field_to_dict,
    first_non_empty_image,
    get_engine_adapter,
    normalize_text,
    safe_float,
    to_ascii_digits,
)
from app.pipeline.lang_detect import detect_language
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.services.business_validation import assess_cin_document

log = get_logger(__name__)
settings = get_settings()


class CINPipelineRunner:
    REQUIRED_INTERNAL_FIELDS = (
        "cin_number", "family_name", "first_name", "date_of_birth", "place_of_birth",
    )
    CRITICAL_INTERNAL_FIELDS = (
        "cin_number", "family_name", "first_name", "date_of_birth", "place_of_birth",
    )
    TARGETED_ROI_MAP = {
        "cin_number": "cin_number", "family_name": "family_name",
        "first_name": "first_name", "date_of_birth": "date_of_birth",
        "place_of_birth": "place_of_birth",
    }
    # inverse of INTERNAL_TO_API_FIELD (api_name → internal_name)
    _API_TO_INTERNAL: Dict[str, str] = {
        "id_number": "cin_number", "last_name": "family_name",
        "first_name": "first_name", "birth_date": "date_of_birth",
        "birth_place": "place_of_birth",
    }

    # ── Arabic OCR correction tables ────────────────────────────────────
    _ARABIC_OCR_NAME_FIXES: Dict[str, str] = {
        "محمل": "محمد", "دمحم": "محمد", "عزين": "عزيز", "زيزع": "عزيز",
        "بنيز": "زينب", "اضر": "رضا", "اضرر": "رضا", "ناضمر": "رمضان",
        "رفاظ": "ظافر", "ينانكلا": "الكناني", "يحلاب": "بالحي",
        "ىحلاب": "بالحي", "ةفيلخ": "خليفة", "يديبزلا": "الزبيدي",
        "ينسحلا": "الحسني", "ىبرعلا": "العربي", "يداعلا": "العادي",
        "يدا يعلا": "العيادي", "العبادي": "العيادي",
        "هدفاء": "هيفاء", "ءافده": "هيفاء", "ءافدھ": "هيفاء",
        "النقاذ": "النقاز", "زاقنلا": "النقاز",
        "ةرد": "درة", "نامثع": "عثمان", "محمل": "محمد",
    }
    _ARABIC_OCR_PLACE_FIXES: Dict[str, str] = {
        "سه سة": "سوسة", "س ه سة": "سوسة", "سو سة": "سوسة", "سوسه": "سوسة",
        "قايبس": "قابس", "قابسن": "قابس",
        "زهره مدين": "زهرة مدين", "جندوبه": "جندوبة",
        "حمام الانف": "حمام الأنف", "حمام الانف": "حمام الأنف",
        "الماتلين": "الماتلين",
    }
    _CIN_STOP_TOKENS = {
        "الاسم", "مسالا", "تاريخ", "تارخ", "تاخ",
        "الولادة", "ةدالولا", "مكانها", "مكان",
        "بطاقة", "التعريف", "الوطنية", "الوطنيه",
        "الجمهورية", "الجمهوريفالتونسية", "التونسية", "تونسية",
        "بن", "بنت", "بقللا", "اللقب",
    }
    _CIN_MONTH_VARIANTS: Dict[int, Tuple[str, ...]] = {
        1: ("جانفي", "جانفى", "جانق", "جان"),
        2: ("فيفري", "فبفري", "فبرى", "فيري", "فبري", "ففري"),
        3: ("مارس",),
        4: ("أفريل", "افريل", "أفربل", "افربل"),
        5: ("ماي",),
        6: ("جوان", "جوانن"),
        7: ("جويلية", "جويليه", "جويلبه"),
        8: ("أوت", "اوت", "أوث"),
        9: ("سبتمبر", "سبتمير"),
        10: ("أكتوبر", "اكتوبر", "اكتوب", "أكتوير", "اكتوير"),
        11: ("نوفمبر", "نوفمير", "نوفمر", "نوفم", "نوف", "وفمبر", "فمبر"),
        12: ("ديسمبر", "ديسمير", "ديسمر"),
    }

    # ==================================================================
    # Mode / engine resolution
    # ==================================================================

    def _language_hints_for_cin(self, request: ExtractionRequest) -> List[str]:
        return [request.language_hint] if request.language_hint else ["ar", "fr", "en"]

    def _resolve_mode(self, request: ExtractionRequest) -> str:
        metadata = getattr(request, "metadata", None) or {}
        explicit = getattr(request, "cin_mode", None) or metadata.get("cin_mode")
        engine_value = (request.engine or "").strip().lower()
        if explicit:
            explicit = str(explicit).strip().lower()
        if explicit in {"fast", "balanced", "full"}:
            return explicit
        if engine_value in {"fast", "balanced", "full"}:
            return engine_value
        default = str(getattr(settings, "CIN_DEFAULT_MODE", "balanced")).strip().lower()
        return default if default in {"fast", "balanced", "full"} else "balanced"

    def _select_cin_engines(self, request: ExtractionRequest, mode: str = "full") -> List[str]:
        forced = (request.engine or "").strip().lower()
        if forced and forced not in {"auto", "fast", "balanced", "full"}:
            return [forced]
        ordered: List[str] = []
        for name in [
            getattr(settings, "CIN_PRIMARY_ENGINE", "paddle"),
            getattr(settings, "CIN_SECONDARY_ENGINE", "easyocr"),
        ]:
            if name and name not in ordered:
                ordered.append(name)
        if (mode == "full"
                and getattr(settings, "ENABLE_TESSERACT", True)
                and getattr(settings, "CIN_FULL_INCLUDE_TESSERACT", False)):
            numeric = getattr(settings, "CIN_NUMERIC_ENGINE", "tesseract")
            if numeric and numeric not in ordered:
                ordered.append(numeric)
        return ordered

    # ==================================================================
    # FIX v2.1 — raw box text reconstruction
    # ==================================================================

    def _reconstruct_text_from_boxes(self, raw_boxes: Any) -> str:
        """
        Build a Y-sorted, newline-separated string from raw OCR boxes.

        Supports:
          EasyOCR  → [bbox, text, confidence]
          PaddleOCR→ [bbox, [text, confidence]]

        Sorting top-to-bottom preserves CIN label order
        (اللقب before الاسم before تاريخ before مكانها), which is required
        for _extract_names_from_cin_text to work correctly.
        """
        if not raw_boxes:
            return ""
        items: List[Tuple[float, str]] = []
        for box in raw_boxes:
            try:
                if not isinstance(box, (list, tuple)) or len(box) < 2:
                    continue
                bbox, text_part = box[0], box[1]
                text = str(text_part[0] if isinstance(text_part, (list, tuple)) else text_part).strip()
                if not text:
                    continue
                y = 0.0
                try:
                    y = float(bbox[0][1])
                except Exception:
                    pass
                items.append((y, text))
            except Exception:
                continue
        items.sort(key=lambda t: t[0])
        return "\n".join(t for _, t in items)

    # ==================================================================
    # Label-spatial OCR extraction from boxes
    # ==================================================================

    def _box_text_conf_bbox(self, box: Any) -> Tuple[str, float, Optional[Any]]:
        """
        Normalize EasyOCR/PaddleOCR box formats into text, confidence, bbox.

        EasyOCR:
            [bbox, text, confidence]
        PaddleOCR:
            [bbox, [text, confidence]]
        """
        try:
            if not isinstance(box, (list, tuple)) or len(box) < 2:
                return "", 0.0, None
            bbox, text_part = box[0], box[1]
            if isinstance(text_part, (list, tuple)):
                text = str(text_part[0] if len(text_part) > 0 else "").strip()
                conf = safe_float(text_part[1] if len(text_part) > 1 else 0.0, 0.0)
            else:
                text = str(text_part).strip()
                conf = safe_float(box[2] if len(box) > 2 else 0.0, 0.0)
            return text, conf, bbox
        except Exception:
            return "", 0.0, None

    def _bbox_metrics(self, bbox: Any) -> Optional[Dict[str, float]]:
        """
        Convert OCR polygon bbox to geometry metrics.
        """
        try:
            pts = [(float(p[0]), float(p[1])) for p in bbox]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            return {
                "x1": x1, "x2": x2, "y1": y1, "y2": y2,
                "cx": (x1 + x2) / 2.0, "cy": (y1 + y2) / 2.0,
                "w": max(1.0, x2 - x1), "h": max(1.0, y2 - y1),
            }
        except Exception:
            return None

    def _ocr_items_from_boxes(self, raw_boxes: Any) -> List[Dict[str, Any]]:
        """
        Convert raw OCR boxes into normalized items with geometry.
        """
        items: List[Dict[str, Any]] = []
        for i, box in enumerate(raw_boxes or []):
            text, conf, bbox = self._box_text_conf_bbox(box)
            if not text:
                continue
            geom = self._bbox_metrics(bbox)
            if not geom:
                continue
            norm = self._normalize_cin_arabic_text(text)
            items.append({
                "i": i, "text": text, "norm": norm, "conf": conf,
                **geom,
            })
        return items

    def _text_has_label(self, text: str, label: str) -> bool:
        """
        Fuzzy-ish label check after _normalize_labels.
        """
        norm = self._normalize_cin_arabic_text(text)
        compact = re.sub(r"\s+", "", norm)

        if label == "family_name":
            return "اللقب" in norm or "اللقب" in compact
        if label == "first_name":
            return "الاسم" in norm or "الاسم" in compact
        if label == "birth_place":
            return "مكانها" in norm or "مكانها" in compact or "مكان" in norm
        if label == "birth_date":
            return "تاريخ" in norm or "الولادة" in norm
        return False

    def _cluster_items_into_lines(self, items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """
        Cluster OCR items into horizontal text lines using y-center distance.
        """
        if not items:
            return []
        ordered = sorted(items, key=lambda it: (it["cy"], it["x1"]))
        median_h = float(np.median([it["h"] for it in ordered] or [20.0]))
        y_tol = max(12.0, median_h * 0.75)

        lines: List[List[Dict[str, Any]]] = []
        for it in ordered:
            placed = False
            for line in lines:
                line_cy = sum(x["cy"] for x in line) / max(1, len(line))
                if abs(it["cy"] - line_cy) <= y_tol:
                    line.append(it)
                    placed = True
                    break
            if not placed:
                lines.append([it])

        for line in lines:
            # Keep both possible orders later; store x ascending as default.
            line.sort(key=lambda it: it["x1"])
        lines.sort(key=lambda line: sum(it["cy"] for it in line) / max(1, len(line)))
        return lines

    def _line_text_variants(self, line: List[Dict[str, Any]]) -> List[str]:
        """
        Return possible text orders for a line. Arabic OCR boxes may need x-desc
        while some engines already return coherent phrase boxes.
        """
        asc = " ".join(it["norm"] for it in sorted(line, key=lambda it: it["x1"]) if it.get("norm"))
        desc = " ".join(it["norm"] for it in sorted(line, key=lambda it: it["x1"], reverse=True) if it.get("norm"))
        raw = " ".join(it["norm"] for it in line if it.get("norm"))
        variants = []
        for v in (raw, asc, desc):
            v = re.sub(r"\s+", " ", v).strip()
            if v and v not in variants:
                variants.append(v)
        return variants

    def _context_lines_around_label(
        self,
        lines: List[List[Dict[str, Any]]],
        label: str,
        max_extra_lines: int = 1,
    ) -> List[str]:
        """
        Find lines containing a label and return compact local context strings.
        This avoids parsing the huge concatenated raw_text.
        """
        contexts: List[str] = []
        for idx, line in enumerate(lines):
            variants = self._line_text_variants(line)
            if not any(self._text_has_label(v, label) for v in variants):
                continue

            # Same line only.
            contexts.extend(variants)

            # Same line + next line. Useful when OCR puts filiation/place continuation
            # on the next visual line, but still limited and local.
            if max_extra_lines > 0 and idx + 1 < len(lines):
                next_variants = self._line_text_variants(lines[idx + 1])
                for v in variants:
                    for nv in next_variants:
                        combo = re.sub(r"\s+", " ", f"{v} {nv}").strip()
                        if combo and combo not in contexts:
                            contexts.append(combo)

            # Previous + same line helps value-before-label RTL ordering.
            if idx > 0:
                prev_variants = self._line_text_variants(lines[idx - 1])
                for pv in prev_variants:
                    for v in variants:
                        combo = re.sub(r"\s+", " ", f"{pv} {v}").strip()
                        if combo and combo not in contexts:
                            contexts.append(combo)
        return contexts

    def _extract_label_spatial_fields_from_boxes(self, raw_boxes: Any) -> Dict[str, Any]:
        """
        Extract CIN fields from OCR boxes by using local label context.

        This is deliberately separate from global raw_text postprocessing:
        it only reads short local contexts around labels like اللقب / الاسم /
        مكانها, which is much more robust against mixed OCR output.
        """
        items = self._ocr_items_from_boxes(raw_boxes)
        if not items:
            return {"fields": {}, "scores": {}, "meta": {"reason": "no_items"}}

        lines = self._cluster_items_into_lines(items)
        fields: Dict[str, Any] = {}
        scores: Dict[str, float] = {}
        meta: Dict[str, Any] = {"line_count": len(lines), "candidates": {}}

        # Family name: only local اللقب contexts.
        family_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._context_lines_around_label(lines, "family_name", max_extra_lines=1):
            val = self._extract_family_name_from_text(ctx)
            if val and self._looks_like_family_name(val):
                score = 40 + (5 if "اللقب" in ctx else 0)
                family_candidates.append((score, val, ctx))
        if family_candidates:
            best = max(family_candidates, key=lambda x: x[0])
            fields["family_name"] = best[1]
            scores["family_name"] = 0.94
            meta["candidates"]["family_name"] = family_candidates[:8]

        # First name: local الاسم contexts.
        first_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._context_lines_around_label(lines, "first_name", max_extra_lines=1):
            val = self._extract_first_name_from_text(ctx)
            if val and self._looks_like_first_name_phrase(val):
                score = 40 + (5 if "الاسم" in ctx else 0)
                first_candidates.append((score, val, ctx))
        if first_candidates:
            best = max(first_candidates, key=lambda x: x[0])
            fields["first_name"] = best[1]
            scores["first_name"] = 0.94
            meta["candidates"]["first_name"] = first_candidates[:8]

        # Birth place: local مكانها contexts.
        place_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._context_lines_around_label(lines, "birth_place", max_extra_lines=0):
            val, reason = self._extract_birth_place_from_raw_context(ctx)
            if val:
                score = 40 + (8 if reason and reason.startswith("place_after_label") else 0)
                place_candidates.append((score, val, ctx))
        if place_candidates:
            best = max(place_candidates, key=lambda x: x[0])
            fields["place_of_birth"] = best[1]
            scores["place_of_birth"] = 0.93
            meta["candidates"]["place_of_birth"] = place_candidates[:8]

        # Birth date: local تاريخ الولادة contexts.
        date_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._context_lines_around_label(lines, "birth_date", max_extra_lines=1):
            val, reason = self._extract_birth_date_from_raw_context(ctx)
            if val:
                score = 40 + (8 if reason else 0)
                date_candidates.append((score, val, ctx))
        if date_candidates:
            best = max(date_candidates, key=lambda x: x[0])
            fields["date_of_birth"] = best[1]
            scores["date_of_birth"] = 0.90
            meta["candidates"]["date_of_birth"] = date_candidates[:8]

        return {"fields": fields, "scores": scores, "meta": meta}

    def _merge_label_spatial_into_spatial(
        self,
        spatial: Dict[str, Any],
        label_spatial: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Merge label-spatial fields into the standard spatial extraction result.
        Label-spatial values are allowed to replace suspicious spatial values
        for name/place fields because they are based on local label context.
        """
        fields = dict(spatial.get("fields") or {})
        scores = dict(spatial.get("scores") or {})
        meta = dict(spatial.get("meta") or {})
        label_fields = label_spatial.get("fields") or {}
        label_scores = label_spatial.get("scores") or {}

        for key, val in label_fields.items():
            if not val:
                continue
            old = fields.get(key)
            should_replace = False

            if key in {"family_name", "first_name"}:
                if not old:
                    should_replace = True
                elif key == "family_name" and not self._looks_like_family_name(old):
                    should_replace = True
                elif key == "first_name" and not self._looks_like_first_name_phrase(old):
                    should_replace = True
                else:
                    # Replace if the old field equals the other extracted name.
                    other_key = "first_name" if key == "family_name" else "family_name"
                    other_val = label_fields.get(other_key)
                    if other_val and self._normalize_cin_arabic_text(old) == self._normalize_cin_arabic_text(other_val):
                        should_replace = True

            elif key == "place_of_birth":
                old_norm = self._normalize_cin_arabic_text(old)
                val_norm = self._normalize_cin_arabic_text(val)
                should_replace = (
                    not old
                    or old_norm in {"الجم", "تونس"}
                    or (val_norm and val_norm != old_norm and len(val_norm) > len(old_norm))
                )

            elif key == "date_of_birth":
                should_replace = not old

            if should_replace:
                fields[key] = val
                scores[key] = max(float(scores.get(key) or 0.0), float(label_scores.get(key) or 0.90))
                meta.setdefault("label_spatial_overrides", {})[key] = {"old": old, "new": val}
            else:
                meta.setdefault("label_spatial_kept_original", {})[key] = {"old": old, "candidate": val}

        meta["label_spatial"] = label_spatial.get("meta", {})
        return {**spatial, "fields": fields, "scores": scores, "meta": meta}

    # ==================================================================
    # Marker / trial helpers
    # ==================================================================

    def _marker_text_from_zones(self, zone_texts: Dict[str, str], extra_text: str = "") -> str:
        return "\n".join([
            f"CIN_NUMBER: {zone_texts.get('cin_number', '')}",
            f"CIN_FAMILY_NAME: {zone_texts.get('family_name', '')}",
            f"CIN_FIRST_NAME: {zone_texts.get('first_name', '')}",
            f"CIN_DATE_OF_BIRTH: {zone_texts.get('date_of_birth', '')}",
            f"CIN_PLACE_OF_BIRTH: {zone_texts.get('place_of_birth', '')}",
            extra_text or "",
        ])

    def _spatial_result_to_trial(self, spatial: Dict[str, Any], engine_name: str) -> Optional[Dict[str, Any]]:
        fields = spatial.get("fields") or {}
        if not fields:
            return None
        score = max(list((spatial.get("scores") or {}).values()) or [0.0])
        return {
            "engine": engine_name, "source": "spatial_boxes",
            "raw_text": "",  # filled in by _run_spatial_boxes
            "score": score, "extracted": fields,
        }

    # ==================================================================
    # Engine trial runners
    # ==================================================================

    def _run_spatial_boxes(
        self, *, engine_name: str, card_img: np.ndarray, spatial_debug: Dict[str, Any],
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
                spatial_debug["trials"].append({
                    "engine": engine_name, "box_count": 0,
                    "fields": {}, "scores": {}, "meta": {},
                    "raw_text_reconstructed": "", "label_spatial": {},
                })
                return None

            raw_text_from_boxes = self._reconstruct_text_from_boxes(raw_boxes)

            spatial = CINSpatialExtractor().extract(raw_boxes)

            # New robust layer: label-spatial extraction around اللقب / الاسم /
            # تاريخ الولادة / مكانها. This reads only local OCR box context,
            # not the huge mixed raw_text.
            label_spatial = self._extract_label_spatial_fields_from_boxes(raw_boxes)
            spatial = self._merge_label_spatial_into_spatial(spatial, label_spatial)

            trial = self._spatial_result_to_trial(spatial, engine_name)
            spatial_debug["trials"].append({
                "engine": engine_name,
                "box_count": len(raw_boxes),
                "fields": spatial.get("fields", {}),
                "scores": spatial.get("scores", {}),
                "meta": spatial.get("meta", {}),
                "label_spatial": label_spatial,
                "raw_text_reconstructed": raw_text_from_boxes,
            })

            if trial:
                trial["raw_text"] = raw_text_from_boxes
            return trial
        except Exception as exc:
            spatial_debug.setdefault("errors", []).append(f"{engine_name}: {exc}")
            return None

    def _run_cin_engine_trial(
        self, *, engine_name: str, card_img: np.ndarray,
        rois: Dict[str, np.ndarray], language_hints: List[str],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        started = time.perf_counter()
        diag: Dict[str, Any] = {
            "engine": engine_name, "executed": False, "trial_type": "full_card_and_all_rois",
            "full_text": "", "full_text_score": 0.0, "processing_time_ms": 0,
            "zones": {}, "zone_scores": {}, "notes": [],
        }
        try:
            engine = get_engine_adapter(engine_name)
        except Exception as exc:
            diag["notes"].append(str(exc))
            return diag, None
        full_text, full_score = call_recognize_document(engine, card_img, language_hints)
        zone_texts: Dict[str, str] = {}
        zone_scores: Dict[str, float] = {}
        for zone_name, roi in rois.items():
            text, score = call_recognize_document(engine, roi, language_hints)
            zone_texts[zone_name] = text
            zone_scores[zone_name] = score
        diag.update({
            "executed": True, "full_text": full_text, "full_text_score": full_score,
            "processing_time_ms": int((time.perf_counter() - started) * 1000),
            "zones": zone_texts, "zone_scores": zone_scores,
        })
        marker_text = self._marker_text_from_zones(zone_texts, extra_text=full_text)
        adapter = CINFieldAdapter()
        trial = {
            "engine": engine_name, "source": "roi_ocr",
            "raw_text": marker_text, "score": full_score,
            "extracted": adapter.extract_fields(marker_text),
        }
        return diag, trial

    def _run_targeted_roi_trial(
        self, *, engine_name: str, missing_fields: List[str],
        rois: Dict[str, np.ndarray], language_hints: List[str],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        started = time.perf_counter()
        diag: Dict[str, Any] = {
            "engine": engine_name, "executed": False, "trial_type": "targeted_missing_rois",
            "target_fields": missing_fields, "processing_time_ms": 0,
            "zones": {}, "zone_scores": {}, "notes": [],
        }
        try:
            engine = get_engine_adapter(engine_name)
        except Exception as exc:
            diag["notes"].append(str(exc))
            return diag, None
        target_roi_names: List[str] = []
        for field in missing_fields:
            roi_name = self.TARGETED_ROI_MAP.get(field)
            if roi_name and roi_name in rois and roi_name not in target_roi_names:
                target_roi_names.append(roi_name)
        if any(f in missing_fields for f in ("family_name", "first_name", "date_of_birth", "place_of_birth")):
            if "right_text_block" in rois and "right_text_block" not in target_roi_names:
                target_roi_names.append("right_text_block")
        zone_texts: Dict[str, str] = {}
        zone_scores: Dict[str, float] = {}
        for roi_name in target_roi_names:
            text, score = call_recognize_document(engine, rois[roi_name], language_hints)
            zone_texts[roi_name] = text
            zone_scores[roi_name] = score
        diag.update({
            "executed": True,
            "processing_time_ms": int((time.perf_counter() - started) * 1000),
            "zones": zone_texts, "zone_scores": zone_scores,
        })
        marker_text = self._marker_text_from_zones(zone_texts, extra_text=zone_texts.get("right_text_block", ""))
        adapter = CINFieldAdapter()
        extracted = adapter.extract_fields(marker_text)
        if not extracted and not normalize_text(marker_text):
            return diag, None
        trial = {
            "engine": engine_name, "source": "targeted_roi_ocr",
            "raw_text": marker_text, "score": max(list(zone_scores.values()) or [0.0]),
            "extracted": extracted,
        }
        return diag, trial

    # ==================================================================
    # Validation helpers
    # ==================================================================

    def _validate_internal_fields(self, fused_fields: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        from app.services.cin_rules import (
            is_valid_birth_place_strict, is_valid_cin_number,
            is_valid_family_name, is_valid_given_name,
            normalize_name, normalize_place, parse_date_any,
        )
        validated: Dict[str, Dict[str, Any]] = {}
        for name in self.REQUIRED_INTERNAL_FIELDS:
            value = fused_fields.get(name)
            if name == "cin_number":
                nv = to_ascii_digits(str(value or ""))
                ok = bool(nv and is_valid_cin_number(nv))
                value = nv if ok else None
            elif name == "family_name":
                nv = normalize_name(str(value or ""))
                ok = bool(nv and is_valid_family_name(nv))
                value = nv if ok else None
            elif name == "first_name":
                nv = normalize_name(str(value or ""))
                ok = bool(nv and is_valid_given_name(nv))
                value = nv if ok else None
            elif name == "date_of_birth":
                nv = None
                if value:
                    try:
                        nv = parse_date_any(str(value))
                    except Exception:
                        nv = None
                ok = bool(nv)
                value = nv if ok else None
            elif name == "place_of_birth":
                nv = normalize_place(str(value or ""))
                ok = bool(nv and is_valid_birth_place_strict(nv))
                value = nv if ok else None
            else:
                ok, value = False, None
            validated[name] = {"value": value, "validated": ok}
        return validated

    def _field_get(self, field: Any, key: str, default: Any = None) -> Any:
        """
        Safely read a value from either a dict field or a Pydantic FieldResult.

        ExtractionResponse.fields may contain FieldResult objects after Pydantic
        model creation, while the internal pipeline mostly works with dicts.
        """
        if isinstance(field, dict):
            return field.get(key, default)
        return getattr(field, key, default)

    def _validated_internal_from_field_dicts(
        self, field_dicts: List[Any],
    ) -> Dict[str, Dict[str, Any]]:
        """
        Derive validated_internal from POSTPROCESSED fields.

        Accepts both dict fields and Pydantic FieldResult objects.
        Used by _can_return_early after _make_response so postprocess corrections
        are reflected in early-return decisions.
        """
        result: Dict[str, Dict[str, Any]] = {}
        for field in field_dicts:
            internal = self._API_TO_INTERNAL.get(self._field_get(field, "name"))
            if internal:
                result[internal] = {
                    "value": self._field_get(field, "value"),
                    "validated": bool(self._field_get(field, "validated")),
                }
        return result

    def _fuse_and_validate(self, trials: List[Dict[str, Any]]):
        from app.services.cin_fuser import CINFieldFuser
        adapter = CINFieldAdapter()
        fuser = CINFieldFuser()
        fused_fields, fusion_debug = fuser.fuse(adapter, trials)
        validated_internal = self._validate_internal_fields(fused_fields)
        fields_api = self._build_cin_v2_field_results(validated_internal, fusion_debug)
        field_dicts = [field_to_dict(f) for f in fields_api]
        return validated_internal, fields_api, field_dicts, self._build_normalized_data(field_dicts), fusion_debug

    def _required_internal_fields_for_mode(self, mode: str) -> List[str]:
        return list(self.CRITICAL_INTERNAL_FIELDS if mode == "fast" else self.REQUIRED_INTERNAL_FIELDS)

    def _missing_internal_fields(self, vi: Dict[str, Dict[str, Any]], mode: str) -> List[str]:
        return [n for n in self._required_internal_fields_for_mode(mode) if not vi.get(n, {}).get("validated")]

    def _missing_critical_fields(self, vi: Dict[str, Dict[str, Any]]) -> List[str]:
        return [n for n in self.CRITICAL_INTERNAL_FIELDS if not vi.get(n, {}).get("validated")]

    def _can_return_early(self, vi: Dict[str, Dict[str, Any]], bv: Dict[str, Any], mode: str) -> bool:
        if mode == "fast":
            return not self._missing_critical_fields(vi)
        return bv.get("status") == "success" and not self._missing_internal_fields(vi, mode)

    # ==================================================================
    # Field result builders
    # ==================================================================

    def _build_cin_v2_field_results(
        self, validated_internal: Dict[str, Dict[str, Any]], fusion_debug: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        selected_map = fusion_debug.get("selected") or {}
        for internal_name, api_name in INTERNAL_TO_API_FIELD.items():
            info = validated_internal.get(internal_name, {})
            value, validated = info.get("value"), bool(info.get("validated"))
            cand = selected_map.get(internal_name)
            if validated and value:
                conf = self._confidence_from_candidate(api_name, cand)
                raw_text, selected_engine, selected_source, reasons = str(value), None, None, ["selected"]
                if isinstance(cand, dict):
                    raw_text = cand.get("raw_value") or str(value)
                    engines, sources = cand.get("engines") or [], cand.get("sources") or []
                    selected_engine = engines[0] if engines else None
                    selected_source = sources[0] if sources else None
                    reasons = [f"selected_from:{selected_source}" if selected_source else "selected"]
                results.append({
                    "name": api_name, "value": value, "confidence": conf, "validated": True,
                    "raw_text": raw_text, "raw_template_field": None, "error": None,
                    "selected_engine": selected_engine, "selected_source": selected_source,
                    "review_required": False, "reasons": reasons,
                })
            else:
                error = {
                    "id_number": "CIN introuvable", "last_name": "Nom introuvable",
                    "first_name": "Prénom introuvable", "birth_date": "Date introuvable",
                    "birth_place": "Lieu introuvable",
                }.get(api_name, "Champ introuvable")
                is_critical = api_name in {"id_number", "last_name", "first_name", "birth_date"}
                results.append({
                    "name": api_name, "value": None, "confidence": 0.0, "validated": False,
                    "raw_text": None, "raw_template_field": None, "error": error,
                    "selected_engine": None, "selected_source": None, "review_required": is_critical,
                    "reasons": ["field unresolved"] + (["critical field missing"] if is_critical else []),
                })
        return results

    def _confidence_from_candidate(self, api_name: str, cand: Optional[Dict[str, Any]]) -> float:
        if not cand:
            return 0.0
        base = 0.55
        base += min(safe_float(cand.get("avg_conf"), 0.0), 1.0) * 0.15
        base += min(int(cand.get("count", 1) or 1), 3) * 0.05
        sources = set(cand.get("sources") or [])
        if "spatial_boxes" in sources:
            base += 0.10
        if "targeted_roi_ocr" in sources:
            base += 0.05
        if "roi_ocr" in sources:
            base += 0.03
        sel_score = safe_float(cand.get("selection_score"), 0.0)
        if sel_score >= 20:
            base += 0.10
        elif sel_score >= 12:
            base += 0.05
        if api_name == "id_number":
            base += 0.10
        elif api_name in {"last_name", "first_name"}:
            base += 0.03
        return round(min(base, 0.95), 3)

    def _build_normalized_data(self, field_dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
        mapping = {
            "id_number": "idNumber", "last_name": "lastName", "first_name": "firstName",
            "birth_date": "birthDate", "birth_place": "birthPlace",
        }
        return {mapping[f["name"]]: f.get("value") for f in field_dicts if f.get("name") in mapping}

    def _compute_engine_used_label_from_fields(self, field_dicts: List[Dict[str, Any]]) -> str:
        selected = sorted({f.get("selected_engine") for f in field_dicts if f.get("selected_engine")})
        if not selected:
            return "unknown"
        return selected[0] if len(selected) == 1 else f"mixed({','.join(selected)})"

    def _build_warnings_from_fields(self, field_dicts: List[Dict[str, Any]], quality_score: float = 0.0) -> List[str]:
        assessment = assess_cin_document(
            field_dicts, quality_score=quality_score,
            success_threshold=getattr(settings, "CIN_SUCCESS_MIN_BUSINESS_CONFIDENCE", 0.88),
        )
        warnings = list(assessment.get("warnings", []))
        for field in field_dicts:
            if field.get("review_required"):
                msg = f"Champ requis '{FIELD_LABELS_FR.get(field.get('name'), field.get('name'))}' à vérifier"
                if msg not in warnings:
                    warnings.append(msg)
            if str(field.get("raw_text") or "").strip().upper().startswith("CIN_"):
                msg = f"Champ '{FIELD_LABELS_FR.get(field.get('name'), field.get('name'))}' contient un placeholder OCR"
                if msg not in warnings:
                    warnings.append(msg)
        return warnings

    # ==================================================================
    # Arabic text normalisation helpers
    # ==================================================================

    def _normalize_labels(self, text: str) -> str:
        """Normalise reversed / misread CIN field labels and kinship markers."""
        for bad, good in [
            ("بقللا", "اللقب"), ("اللقب:", "اللقب"), ("الاقب", "اللقب"),
            ("مسالا", "الاسم"), ("مسإلا", "الاسم"), ("مسا", "الاسم"),
            ("اللسم", "الاسم"), ("السم", "الاسم"), ("الاسم:", "الاسم"),
            ("ةدالولا", "الولادة"), ("الولا دة", "الولادة"), ("الوللادة", "الولادة"),
            ("تارخالولادة", "تاريخ الولادة"), ("تاخالولادة", "تاريخ الولادة"),
            ("تارخ الولادة", "تاريخ الولادة"), ("تاريخالولادة", "تاريخ الولادة"),
            ("ناريخالولادة", "تاريخ الولادة"), ("ثاريخ الولادة", "تاريخ الولادة"),
            ("تابيخ الولادة", "تاريخ الولادة"), ("ناخ الولادة", "تاريخ الولادة"),
            ("ناغ الولادة", "تاريخ الولادة"), ("امغ الولادة", "تاريخ الولادة"),
            ("اهناكم", "مكانها"), ("اهناك", "مكانها"), ("عانها", "مكانها"),
            ("عانا", "مكانها"), ("مانها", "مكانها"), ("تاها", "مكانها"),
            ("ناها", "مكانها"), ("اناكم", "مكانها"), ("انها", "مكانها"),
            ("كانها", "مكانها"), ("كاغا", "مكانها"), ("مكممكانها", "مكانها"),
            ("مكمكانها", "مكانها"), ("تممكانها", "مكانها"), ("محممكانها", "مكانها"),
            ("ىنت", "بنت"), ("نت", "بنت"), ("بثت", "بنت"),
        ]:
            text = text.replace(bad, good)
        return text


    def _normalize_cin_arabic_text(self, text: Any) -> str:
        value = normalize_text(str(text or ""))
        value = to_ascii_digits(value)
        value = self._normalize_labels(value)
        for bad, good in self._ARABIC_OCR_NAME_FIXES.items():
            value = re.sub(
                rf"(?<![\u0600-\u06FF]){re.escape(bad)}(?![\u0600-\u06FF])",
                good, value,
            )
        return re.sub(r"\s+", " ", value).strip()

    def _clean_cin_name_token(self, token: str) -> Optional[str]:
        token = self._normalize_cin_arabic_text(token)
        token = re.sub(r"[^\u0600-\u06FF]", "", token).strip()
        if not token or len(token) < 2 or token in self._CIN_STOP_TOKENS:
            return None
        return token

    def _reverse_words_per_line(self, text: str) -> str:
        """Reverse word order on each line to fix EasyOCR/Paddle RTL reversal."""
        return "\n".join(" ".join(reversed(line.split())) for line in text.split("\n"))

    # ==================================================================
    # FIX v2.1 — Name extraction (rewritten)
    # ==================================================================

    def _clean_family_candidate(self, raw: Any) -> Optional[str]:
        """
        Clean and validate a family-name candidate.
        Reject OCR-reversed fragments and context pollution.
        """
        text = self._normalize_cin_arabic_text(raw)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if not text:
            return None

        stop_tokens = {
            "الاسم", "مسالا", "اللقب", "بقللا",
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة",
            "مكانها", "مكان", "الجمهورية", "التونسية", "بطاقة",
            "التعريف", "الوطنية", "الوطنيه", "right_text_block",
            "CIN", "NUMBER", "FAMILY", "NAME", "FIRST", "DATE", "BIRTH", "PLACE",
        }

        tokens: List[str] = []
        for tok in text.split():
            clean = self._clean_cin_name_token(tok)
            if not clean or clean in stop_tokens:
                break
            tokens.append(clean)

        if not tokens:
            return None

        if len(tokens) > 3:
            return None

        val = " ".join(tokens)

        # Reject fragments that are usually OCR-reversed Arabic rather than a real name.
        # This is generic: it does not memorize a card; it rejects low-quality
        # reversed-looking candidates that raw_text often appends.
        reversed_like = {
            "يزوبجلا", "دنور", "رامعنب", "ديعلا", "زاقنلا", "ءافده",
            "ينسحلا", "بنيز", "يقدص", "رون",
        }
        if any(t in reversed_like for t in tokens):
            return None

        if not self._looks_like_family_name(val):
            return None
        return val


    def _extract_family_name_from_text(self, text: str) -> Optional[str]:
        """
        Extract family name from label context.

        Supports:
          - normal OCR:  اللقب <family> الاسم <first> ...
          - RTL/value-before-label OCR: <family> اللقب <first> الاسم ...

        Does not accept reversed/gibberish fragments.
        """
        candidates: List[Tuple[int, str, str]] = []

        # Strong normal pattern: label then value, cut before الاسم/date/place.
        for m in re.finditer(
            r"اللقب\s+([\u0600-\u06FF][\u0600-\u06FF\s]{1,45}?)(?=\s+(?:الاسم|تاريخ|مكانها|$))",
            text,
        ):
            val = self._clean_family_candidate(m.group(1))
            if val:
                candidates.append((30 + len(val.split()), val, f"normal:{m.group(0)}"))

        # Strong value-before-label pattern: family immediately before اللقب,
        # first name immediately before الاسم.
        for m in re.finditer(
            r"([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+اللقب\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+الاسم",
            text,
        ):
            val = self._clean_family_candidate(m.group(1))
            if val:
                candidates.append((36 + len(val.split()), val, f"before_label:{m.group(0)}"))

        # Weak fallback: value before اللقب only if it is not preceded by obvious
        # OCR markers or reversed fragments.
        for m in re.finditer(
            r"(?<![A-Za-z_])([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+اللقب(?=\s|$)",
            text,
        ):
            val = self._clean_family_candidate(m.group(1))
            if val:
                candidates.append((18 + len(val.split()), val, f"weak_before_label:{m.group(0)}"))

        if not candidates:
            return None

        # Prefer highest score, then later occurrence.
        ranked: List[Tuple[int, int, str, str]] = []
        for idx, (score, val, reason) in enumerate(candidates):
            ranked.append((score, idx, val, reason))
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return ranked[0][2]


    def _clean_first_name_candidate(self, raw: Any) -> Optional[str]:
        """
        Clean and validate a first-name candidate.
        """
        text = self._normalize_cin_arabic_text(raw)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        if not text:
            return None

        stop_tokens = {
            "الاسم", "مسالا", "اللقب", "بقللا",
            "بن", "بنت",
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة",
            "مكانها", "مكان", "الجمهورية", "التونسية", "بطاقة",
            "التعريف", "الوطنية", "الوطنيه", "right_text_block",
        }

        tokens: List[str] = []
        for tok in text.split():
            clean = self._clean_cin_name_token(tok)
            if not clean or clean in stop_tokens:
                break
            tokens.append(clean)

        if not tokens or len(tokens) > 3:
            return None

        reversed_like = {
            "يزوبجلا", "دنور", "رامعنب", "ديعلا", "زاقنلا", "ءافده",
            "ينسحلا", "بنيز", "يقدص", "رون", "فيضولا", "نيلتاملا", "راكب",
        }
        if any(t in reversed_like for t in tokens):
            return None

        joined = " ".join(tokens)
        if not self._looks_like_first_name_phrase(joined):
            return None
        return joined


    def _extract_first_name_from_text(self, text: str) -> Optional[str]:
        """
        Extract first name from the الاسم context.

        The safest CIN rule is:
            الاسم <first_name words> بن/بنت ...

        OCR may also output:
            اللقب <family> الاسم <first> بنت/بن ...
            <family> اللقب <first> الاسم بنت/بن ...
        """
        candidates: List[Tuple[int, str, str]] = []

        # Strong full context: اللقب <family> الاسم <first> بن/بنت
        for m in re.finditer(
            r"اللقب\s+[\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2}\s+الاسم\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+(?=بنت|بن\b)",
            text,
        ):
            val = self._clean_first_name_candidate(m.group(1))
            if val:
                candidates.append((46 + max(0, 3 - len(val.split())), val, f"family_label_context:{m.group(0)}"))

        # Normal order: الاسم <first> بن/بنت
        for m in re.finditer(
            r"الاسم\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+(?=بنت|بن\b)",
            text,
        ):
            val = self._clean_first_name_candidate(m.group(1))
            if val:
                candidates.append((40 + max(0, 3 - len(val.split())), val, f"normal:{m.group(0)}"))

        # Value-before-label: <family> اللقب <first> الاسم بن/بنت
        for m in re.finditer(
            r"اللقب\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+الاسم\s+(?=بنت|بن\b)",
            text,
        ):
            raw_tokens = m.group(1).strip().split()
            # closest word(s) before الاسم; avoid taking family.
            for window in (1, 2, 3):
                if len(raw_tokens) >= window:
                    val = self._clean_first_name_candidate(" ".join(raw_tokens[-window:]))
                    if val:
                        candidates.append((38 + max(0, 3 - len(val.split())), val, f"before_ism:{m.group(0)}"))
                        break

        # Generic value-before-label: <first> الاسم بن/بنت.
        for m in re.finditer(
            r"(?:^|\s)([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+الاسم\s+(?=بنت|بن\b)",
            text,
        ):
            raw_tokens = m.group(1).strip().split()
            for window in (1, 2, 3):
                if len(raw_tokens) >= window:
                    val = self._clean_first_name_candidate(" ".join(raw_tokens[-window:]))
                    if val:
                        candidates.append((34 + max(0, 3 - len(val.split())), val, f"generic_before_ism:{m.group(0)}"))
                        break

        # Fallback: الاسم <word> before date/place if بن/بنت was missed.
        for m in re.finditer(
            r"الاسم\s+([\u0600-\u06FF]{2,25})(?=\s+(?:تاريخ|مكانها|$))",
            text,
        ):
            val = self._clean_first_name_candidate(m.group(1))
            if val:
                candidates.append((20, val, f"fallback:{m.group(0)}"))

        if not candidates:
            return None

        ranked: List[Tuple[int, int, str, str]] = []
        for idx, (score, val, reason) in enumerate(candidates):
            ranked.append((score, idx, val, reason))
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return ranked[0][2]


    def _extract_names_from_cin_text(self, raw_text: str) -> Optional[Tuple[str, str, str]]:
        """
        Extract (family_name, first_name, debug_str) from CIN raw text.

        Tries normal text first. Reversed word order is used only as fallback,
        because full raw_text often already contains separate reversed fragments.
        """
        text = self._normalize_cin_arabic_text(raw_text)
        text = re.sub(r"[^\u0600-\u06FF0-9\s\n]", " ", text)
        text = re.sub(r"[ \t]+", " ", text).strip()

        family_name = self._extract_family_name_from_text(text)
        first_name = self._extract_first_name_from_text(text)
        used = ["normal"]

        if not family_name or not first_name:
            rev = self._normalize_cin_arabic_text(self._reverse_words_per_line(text))
            if not family_name:
                family_name = self._extract_family_name_from_text(rev)
                if family_name:
                    used.append("family_word_order_reversed")
            if not first_name:
                first_name = self._extract_first_name_from_text(rev)
                if first_name:
                    used.append("first_word_order_reversed")

        debug = f"اللقب:{family_name or '?'} الاسم:{first_name or '?'} variants:{','.join(used)}"

        if not family_name and not first_name:
            return None
        return family_name, first_name, debug


    def _extract_names_before_bin_from_raw_text(self, raw_text: str) -> Optional[Tuple[str, str, str]]:
        return self._extract_names_from_cin_text(raw_text)

    # ==================================================================
    # Birth date helpers
    # ==================================================================

    def _find_plausible_birth_year_from_raw_text(self, raw_text: str) -> Optional[int]:
        text = self._normalize_cin_arabic_text(raw_text)
        current_year = date.today().year
        candidates: List[Tuple[int, int]] = []
        for m in re.finditer(r"\b(19[0-9]{2}|20[0-9]{2})\b", text):
            year = int(m.group(1))
            if 1900 <= year <= current_year:
                ctx = text[max(0, m.start() - 80): min(len(text), m.end() + 80)]
                score = 10 if any(w in ctx for w in ("ولادة", "الولادة", "تاريخ", "تاخ")) else 0
                if "بن" in ctx:
                    score += 2
                candidates.append((score, year))
        if not candidates:
            return None
        return max(candidates, key=lambda t: t[0])[1]

    def _fix_future_birth_date_value(self, value: Any, raw_text: str) -> Tuple[Any, Optional[str]]:
        if not value:
            return value, None
        vs = str(value).strip()
        m = re.match(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$", vs)
        if not m:
            return value, None
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= year <= date.today().year:
            return value, None
        plausible = self._find_plausible_birth_year_from_raw_text(raw_text)
        if plausible:
            fixed = f"{plausible:04d}-{month:02d}-{day:02d}"
            return fixed, f"future_birth_year_replaced:{vs}->{fixed}"
        return None, f"future_birth_year_rejected:{vs}"

    def _month_from_context(self, context: str) -> Optional[int]:
        context = self._normalize_cin_arabic_text(context)
        for month, variants in self._CIN_MONTH_VARIANTS.items():
            for token in variants:
                if token and token in context:
                    return month
        return None

    def _extract_birth_date_from_raw_context(self, raw_text: str) -> Tuple[Optional[str], Optional[str]]:
        text = self._normalize_cin_arabic_text(raw_text)
        text = re.sub(r"\s+", " ", text).strip()
        current_year = date.today().year
        month_words = [v for variants in self._CIN_MONTH_VARIANTS.values() for v in variants]
        month_re = "|".join(re.escape(w) for w in sorted(set(month_words), key=len, reverse=True) if w)

        direct = re.search(rf"\b([0-9]{{1,2}})\s+({month_re})\s+([0-9]{{4}})\b", text)
        if direct:
            day, month, year = int(direct.group(1)), self._month_from_context(direct.group(2)), int(direct.group(3))
            if month and 1 <= day <= 31 and 1900 <= year <= current_year:
                return f"{year:04d}-{month:02d}-{day:02d}", f"direct_textual:{direct.group(0)}"

        contexts: List[str] = []
        for anchor in ("تاريخ الولادة", "الولادة", "ولادة", "لولادة", "ناالولادة", "تاخالولادة", "تارخالولادة"):
            idx = text.find(anchor)
            if idx >= 0:
                contexts.append(text[max(0, idx - 90): min(len(text), idx + 150)])
        contexts.append(text)

        candidates: List[Tuple[int, str, str]] = []

        for ctx in contexts:
            month = self._month_from_context(ctx)
            years = [y for y in [int(x) for x in re.findall(r"\b(19[0-9]{2}|20[0-9]{2})\b", ctx)] if 1900 <= y <= current_year]

            if month:
                days = [n for n in [int(x) for x in re.findall(r"\b([0-9]{1,2})\b", ctx)] if 1 <= n <= 31]
                for year in years:
                    for day in days:
                        score = (20 if "ولادة" in ctx else 0) + 10
                        candidates.append((score, f"{year:04d}-{month:02d}-{day:02d}", ctx))

            # Numeric date fallback: dd mm yyyy or yyyy mm dd near الولادة.
            nums = [int(x) for x in re.findall(r"\b([0-9]{1,4})\b", ctx)]
            for i in range(len(nums) - 2):
                a, b, c = nums[i], nums[i + 1], nums[i + 2]
                triples = []
                if 1 <= a <= 31 and 1 <= b <= 12 and 1900 <= c <= current_year:
                    triples.append((c, b, a))
                if 1900 <= a <= current_year and 1 <= b <= 12 and 1 <= c <= 31:
                    triples.append((a, b, c))
                for year, month_num, day in triples:
                    score = 8 + (15 if "ولادة" in ctx else 0)
                    candidates.append((score, f"{year:04d}-{month_num:02d}-{day:02d}", ctx))

        if candidates:
            best = max(candidates, key=lambda x: x[0])
            return best[1], "contextual_birth_date"

        return None, None


    # ==================================================================
    # Birth place helper
    # ==================================================================

    def _extract_birth_place_from_raw_context(self, raw_text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Recover birth place from OCR context. Labeled candidates are preferred.
        Generic تونس is not accepted from raw text unless it follows مكانها.
        """
        text = self._normalize_cin_arabic_text(raw_text)
        text = re.sub(r"[^\u0600-\u06FF0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        for bad, good in self._ARABIC_OCR_PLACE_FIXES.items():
            text = text.replace(bad, good)

        place_value_fixes = {
            "القلعة الكب": "القلعة الكبرى",
            "القلعة الكبر": "القلعة الكبرى",
            "القلعة الكبرئ": "القلعة الكبرى",
            "حمام الانف": "حمام الأنف",
        }

        known_places = [
            "القلعة الكبرى", "حزوة توزر", "زهرة مدين", "حمام الأنف",
            "الماتلين", "فيتوريا", "سوسة", "قابس", "مدنين", "الكرم",
            "جندوبة", "تونس",
        ]

        label_re = (
            r"(?:مكانها|مكانھا|مكممكانها|مكمكانها|تممكانها|محممكانها|"
            r"عانها|عانا|مانها|مانھا|تاها|ناها|انها|كانها|كاغا|اهناكك|اهناك)"
        )
        stop_re = (
            r"(?=\s*(?:"
            r"اللقب|بقللا|الاسم|مسالا|تاريخ|تاخ|تابيخ|ثاريخ|الولادة|"
            r"CIN_|right_text_block|$"
            r"))"
        )
        pattern = label_re + r"\s+([\u0600-\u06FF\s]{2,45}?)" + stop_re

        stop_words = {
            "الجمهورية", "التونسية", "تونسية", "بطاقة", "بطاقه",
            "التعريف", "الوطنية", "الوطنيه",
            "اللقب", "بقللا", "الاسم", "مسالا",
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة",
            "بن", "بنت",
        }
        reversed_like = {
            "يزوبجلا", "دنور", "رامعنب", "ديعلا", "زاقنلا", "ءافده",
            "ينسحلا", "بنيز", "يقدص", "رون", "فيضولا", "راكب",
        }

        likely_name_tokens = {
            "صابر", "ظافر", "زينب", "روند", "رضا", "صدقي", "هيفاء",
            "شادي", "درة", "محمد", "عميد", "عثمان", "الجبوزي", "الكناني",
            "جماعي", "الحسني", "النقاز", "بنعمار", "الوضيف",
        }

        def normalize_place_value(place: str) -> str:
            place = normalize_text(place)
            for bad, good in self._ARABIC_OCR_PLACE_FIXES.items():
                place = place.replace(bad, good)
            for bad, good in place_value_fixes.items():
                if place == bad:
                    place = good
            return place

        def clean_place_candidate(raw: str) -> Optional[str]:
            raw_norm = normalize_place_value(raw)

            for kp in known_places:
                if kp in raw_norm:
                    return kp

            tokens = []
            for token in raw_norm.strip().split():
                clean = self._clean_cin_name_token(token)
                if not clean or clean in stop_words or clean in reversed_like:
                    break
                if clean in likely_name_tokens and tokens:
                    break
                tokens.append(clean)

            if not tokens or len(tokens) > 3:
                return None

            place = normalize_place_value(" ".join(tokens))
            if not place or self._name_has_forbidden_context(place):
                return None
            if any(t in reversed_like for t in place.split()):
                return None
            return place

        candidates: List[Tuple[int, int, str, str]] = []

        for idx, match in enumerate(re.finditer(pattern, text)):
            place = clean_place_candidate(match.group(1))
            if not place:
                continue

            score = 40 + idx
            reason = f"place_after_label:{match.group(0)}"

            if len(place.split()) >= 2:
                score += 8
            if place in {"الجم"}:
                score -= 30
            if place in known_places:
                score += 15

            candidates.append((score, idx, place, reason))

        # Gazetteer fallback only for non-header-specific places.
        for place in known_places:
            if place == "تونس":
                continue
            if place in text:
                score = 20 + (8 if len(place.split()) >= 2 else 0)
                candidates.append((score, -1, place, "known_place_in_raw_text"))

        if not candidates:
            return None, None

        best_by_place: Dict[str, Tuple[int, int, str, str]] = {}
        for cand in candidates:
            score, idx, place, reason = cand
            old = best_by_place.get(place)
            if old is None or (score, idx) > (old[0], old[1]):
                best_by_place[place] = cand

        best = max(best_by_place.values(), key=lambda x: (x[0], x[1]))
        return best[2], best[3]


    # ==================================================================
    # Raw text assembly
    # ==================================================================

    def _best_available_raw_text(
        self,
        trials: List[Dict[str, Any]],
        spatial_debug: Dict[str, Any],
        engine_diags: List[Dict[str, Any]],
    ) -> str:
        """
        Collect all text, giving priority to sources that contain Arabic
        field labels (اللقب / الاسم), which are needed by _extract_names_from_cin_text.
        """
        labeled: List[str] = []
        unlabeled: List[str] = []

        def _add(t: str) -> None:
            if not t:
                return
            (labeled if ("اللقب" in t or "الاسم" in t) else unlabeled).append(t)

        for trial in trials or []:
            _add(normalize_text(trial.get("raw_text")))
            extracted = trial.get("extracted") or {}
            if extracted:
                unlabeled.append(" ".join(str(v) for v in extracted.values() if v not in (None, "", [], {})))

        for trial in (spatial_debug or {}).get("trials", []) or []:
            _add(trial.get("raw_text_reconstructed", ""))
            fields = trial.get("fields") or {}
            if fields:
                unlabeled.append(" ".join(str(v) for v in fields.values() if v not in (None, "", [], {})))
            for item in (trial.get("meta") or {}).values():
                if isinstance(item, dict):
                    for key in ("candidate_text", "chosen_from"):
                        if v := item.get(key):
                            unlabeled.append(str(v))

        for diag in engine_diags or []:
            if t := diag.get("full_text"):
                unlabeled.append(str(t))
            for v in (diag.get("zones") or {}).values():
                if v:
                    unlabeled.append(str(v))

        joined = normalize_text(" ".join(labeled + unlabeled))
        return self._normalize_cin_arabic_text(joined)

    # ==================================================================
    # CIN postprocess plausibility guards
    # ==================================================================

    def _field_is_good_spatial(self, field: Dict[str, Any]) -> bool:
        """
        A spatial_boxes result with high confidence should not be overwritten by
        regex-based postprocessing unless the current value is clearly invalid.
        """
        if not field:
            return False
        return (
            bool(field.get("validated"))
            and bool(field.get("value"))
            and field.get("selected_source") == "spatial_boxes"
            and float(field.get("confidence") or 0.0) >= 0.85
        )

    def _looks_like_family_name(self, value: Any) -> bool:
        """
        Conservative validation for Tunisian CIN family-name candidates.
        Allows short composed names such as "بن عمار", but rejects OCR context
        and full filiation strings.
        """
        text = self._normalize_cin_arabic_text(value)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        tokens = [t for t in text.split() if t]

        if not tokens or len(tokens) > 3:
            return False

        bad_tokens = {
            "الاسم", "مسالا", "اللقب", "بقللا",
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة",
            "مكانها", "مكان", "مكممكانها", "مكمكانها", "تممكانها",
            "الجمهورية", "الجمهوريف", "التونسية", "التونسيه",
            "بطاقة", "بطاقه", "التعريف", "الوطنيه", "الوطنية",
            "right_text_block",
        }
        if any(t in bad_tokens for t in tokens):
            return False

        # A family name may be "بن عمار"; it should not be a full filiation
        # like "الحسني رضا بن عمر بن العربي".
        if len(tokens) >= 3 and ("بن" in tokens or "بنت" in tokens):
            return False

        # Obvious place fragments should not be accepted as names.
        place_like = {"تونس", "سوسة", "قابس", "توزر", "حزوة", "فيتوريا", "مدنين", "الجم", "القلعة"}
        if any(t in place_like for t in tokens):
            return False

        return True

    def _looks_like_first_name(self, value: Any) -> bool:
        """
        Tunisian CIN first name is normally the word(s) before the first بن/بنت.
        Keep this strict to avoid replacing it with father/grandfather names.
        """
        text = self._normalize_cin_arabic_text(value)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        tokens = [t for t in text.split() if t]

        return self._looks_like_first_name_phrase(text)

    def _looks_like_first_name_phrase(self, value: Any) -> bool:
        """
        First names may contain more than one word. They are valid only when
        they are the phrase before the first بن/بنت and do not contain context.
        """
        text = self._normalize_cin_arabic_text(value)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        tokens = [t for t in text.split() if t]

        if not tokens or len(tokens) > 3:
            return False

        bad_tokens = {
            "بن", "بنت", "الاسم", "مسالا", "اللقب", "بقللا",
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة",
            "مكانها", "مكان", "مكممكانها", "مكمكانها", "تممكانها",
            "الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية",
        }
        return not any(t in bad_tokens for t in tokens)

    def _name_has_forbidden_context(self, value: Any) -> bool:
        """
        Reject names polluted by field labels, places, headers or OCR glue.
        """
        text = self._normalize_cin_arabic_text(value)
        forbidden = [
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة",
            "مكانها", "مكان", "مكممكانها", "مكمكانها", "تممكانها",
            "الجمهورية", "الجمهوريف", "التونسية", "التونسيه",
            "بطاقة", "بطاقه", "التعريف", "الوطنية", "الوطنيه",
            "CIN_", "right_text_block",
        ]
        return any(x in text for x in forbidden)

    # ==================================================================
    # FIX v2.1 — Post-process (rewritten)
    # ==================================================================

    def _apply_cin_postprocess(
        self,
        field_dicts: List[Dict[str, Any]],
        raw_text: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Post-OCR corrections:

        1. Normalise already-selected name values (character-level OCR fixes).
        2. Re-extract family_name / first_name from raw_text using labels:
               اللقب → family_name (all words until الاسم)
               الاسم → first_name (words BEFORE first بن / بنت only)
           raw_text now contains actual box text (fix v2.1) so labels are
           available.  Reversed word order is tried automatically.
        3. Fix / recover birth date.
        4. Fix / recover birth place.
        """
        debug: Dict[str, Any] = {"executed": True, "name_extraction": None, "applied": []}
        fbname = {f.get("name"): f for f in field_dicts}

        # 1) Normalise already-selected values
        for api_name in ("last_name", "first_name", "birth_place"):
            field = fbname.get(api_name)
            if not field or not field.get("value"):
                continue
            old = str(field.get("value") or "")
            new = self._normalize_cin_arabic_text(old)
            if new != old:
                field["value"] = new
                field["raw_text"] = old
                field.setdefault("reasons", [])
                field["reasons"] = list(field["reasons"]) + [f"cin_arabic_ocr_fix:{old}->{new}"]
                debug["applied"].append({"field": api_name, "type": "arabic_ocr_name_fix", "old": old, "new": new})

        # 2) Re-extract names from raw_text, but do NOT blindly override good
        # spatial values. raw_text is a concatenation of several OCR passes and
        # can contain labels/words in a wrong order.
        extracted = self._extract_names_from_cin_text(raw_text)
        if extracted:
            family_name, first_name, debug_seq = extracted
            debug["name_extraction"] = {
                "debug_sequence": debug_seq,
                "family_name": family_name,
                "first_name": first_name,
                "rule": "family_name=all words after اللقب; first_name=words on الاسم line before بن/بنت",
            }

            # last_name / اللقب
            last_field = fbname.get("last_name")
            if last_field and family_name:
                old = last_field.get("value")
                candidate_ok = self._looks_like_family_name(family_name)
                old_ok = self._looks_like_family_name(old)

                old_equals_first = bool(first_name and old and self._normalize_cin_arabic_text(old) == self._normalize_cin_arabic_text(first_name))
                # Override even high-confidence spatial if the current last_name
                # equals the extracted first_name. This catches cases where the
                # ROI read the first-name line as family name.
                should_override = candidate_ok and (
                    not self._field_is_good_spatial(last_field)
                    or not old_ok
                    or old_equals_first
                )

                if should_override:
                    last_field.update({
                        "value": family_name,
                        "validated": True,
                        "confidence": max(float(last_field.get("confidence") or 0.0), 0.90),
                        "selected_source": "cin_postprocess_laqab_line",
                        "error": None,
                        "review_required": False,
                        "reasons": list(last_field.get("reasons") or []) + [
                            "reconstructed_from:اللقب_line"
                        ],
                    })
                    if old != family_name:
                        debug["applied"].append({
                            "field": "last_name",
                            "type": "laqab_line_extraction",
                            "old": old,
                            "new": family_name,
                        })
                else:
                    debug["applied"].append({
                        "field": "last_name",
                        "type": "postprocess_skip_name_override",
                        "kept": old,
                        "candidate": family_name,
                        "candidate_ok": candidate_ok,
                        "old_ok": old_ok,
                        "good_spatial": self._field_is_good_spatial(last_field),
                    })

            # first_name / الاسم
            first_field = fbname.get("first_name")
            if first_field and first_name:
                old = first_field.get("value")
                candidate_ok = self._looks_like_first_name_phrase(first_name)
                old_ok = self._looks_like_first_name_phrase(old)

                should_override = candidate_ok and (
                    not self._field_is_good_spatial(first_field)
                    or not old_ok
                    or (
                        old
                        and first_name
                        and self._normalize_cin_arabic_text(old) != self._normalize_cin_arabic_text(first_name)
                        and self._normalize_cin_arabic_text(first_name) in self._normalize_cin_arabic_text(raw_text)
                    )
                )

                if should_override:
                    first_field.update({
                        "value": first_name,
                        "validated": True,
                        "confidence": max(float(first_field.get("confidence") or 0.0), 0.90),
                        "selected_source": "cin_postprocess_ism_before_bin",
                        "error": None,
                        "review_required": False,
                        "reasons": list(first_field.get("reasons") or []) + [
                            "reconstructed_from:الاسم_before_بن/بنت"
                        ],
                    })
                    if old != first_name:
                        debug["applied"].append({
                            "field": "first_name",
                            "type": "ism_before_bin_extraction",
                            "old": old,
                            "new": first_name,
                        })
                else:
                    debug["applied"].append({
                        "field": "first_name",
                        "type": "postprocess_skip_name_override",
                        "kept": old,
                        "candidate": first_name,
                        "candidate_ok": candidate_ok,
                        "old_ok": old_ok,
                        "good_spatial": self._field_is_good_spatial(first_field),
                    })

        # 2b) Reject names polluted by OCR context.
        for api_name in ("last_name", "first_name"):
            field = fbname.get(api_name)
            if field and field.get("value") and self._name_has_forbidden_context(field.get("value")):
                old = field.get("value")
                field.update({
                    "validated": False,
                    "confidence": 0.0,
                    "error": "Nom contenant du contexte OCR invalide",
                    "review_required": True,
                    "reasons": list(field.get("reasons") or []) + [
                        "invalid_name_contains_ocr_context"
                    ],
                })
                debug["applied"].append({
                    "field": api_name,
                    "type": "invalid_name_contains_ocr_context",
                    "value": old,
                })

        # 2c) Extra name consistency checks.
        last_field = fbname.get("last_name")
        first_field = fbname.get("first_name")
        if last_field and first_field and last_field.get("value") and first_field.get("value"):
            last_norm = self._normalize_cin_arabic_text(last_field.get("value"))
            first_norm = self._normalize_cin_arabic_text(first_field.get("value"))

            if last_norm == first_norm:
                for api_name, field in (("last_name", last_field), ("first_name", first_field)):
                    field.update({
                        "validated": False,
                        "confidence": min(float(field.get("confidence") or 0.0), 0.60),
                        "error": "Nom et prénom identiques - vérification requise",
                        "review_required": True,
                        "reasons": list(field.get("reasons") or []) + ["invalid_same_last_and_first_name"],
                    })
                    debug["applied"].append({
                        "field": api_name,
                        "type": "invalid_same_last_and_first_name",
                        "value": field.get("value"),
                    })

        # 3) Fix / recover birth date
        birth_field = fbname.get("birth_date")
        if birth_field:
            old_date = birth_field.get("value")
            fixed_date, reason = self._fix_future_birth_date_value(old_date, raw_text)
            if reason:
                if fixed_date:
                    birth_field.update({
                        "value": fixed_date, "validated": True,
                        "confidence": max(float(birth_field.get("confidence") or 0.0), 0.85),
                        "selected_source": "cin_postprocess_birth_date_plausibility",
                        "error": None, "review_required": False,
                        "reasons": list(birth_field.get("reasons") or []) + [reason],
                    })
                    debug["applied"].append({"field": "birth_date", "type": "future_year_fix", "old": old_date, "new": fixed_date})
                else:
                    birth_field.update({
                        "value": None, "validated": False, "confidence": 0.0,
                        "selected_source": "cin_postprocess_birth_date_plausibility",
                        "error": "Date de naissance future invalide", "review_required": True,
                        "reasons": list(birth_field.get("reasons") or []) + [reason],
                    })
                    debug["applied"].append({"field": "birth_date", "type": "future_year_rejected", "old": old_date, "new": None})

            if not birth_field.get("value"):
                rec_date, date_reason = self._extract_birth_date_from_raw_context(raw_text)
                if rec_date:
                    birth_field.update({
                        "value": rec_date, "validated": True,
                        "confidence": max(float(birth_field.get("confidence") or 0.0), 0.82),
                        "selected_source": "cin_postprocess_birth_date_context",
                        "error": None, "review_required": False,
                        "reasons": list(birth_field.get("reasons") or []) + [f"birth_date_recovered:{date_reason}"],
                    })
                    debug["applied"].append({"field": "birth_date", "type": "contextual_date_recovery", "old": old_date, "new": rec_date, "reason": date_reason})

        # 4) Fix / recover birth place.
        # A candidate explicitly found after مكانها is usually stronger than a
        # spatial ROI value, because ROI OCR can accidentally read text from the
        # photo/background area. Still, do not override with the same value.
        place_field = fbname.get("birth_place")
        if place_field:
            old_place = place_field.get("value")
            rec_place, place_reason = self._extract_birth_place_from_raw_context(raw_text)

            old_place_norm = self._normalize_cin_arabic_text(old_place)
            rec_place_norm = self._normalize_cin_arabic_text(rec_place)

            contextual_is_labeled = bool(place_reason and place_reason.startswith("place_after_label"))
            old_is_generic_or_short = old_place_norm in {"تونس", "الجم"} or len(old_place_norm) <= 3

            should_update_place = (
                rec_place
                and rec_place_norm != old_place_norm
                and (
                    not old_place
                    or not place_field.get("validated")
                    or contextual_is_labeled
                    or old_is_generic_or_short
                )
            )

            if should_update_place:
                place_field.update({
                    "value": rec_place, "validated": True,
                    "confidence": max(float(place_field.get("confidence") or 0.0), 0.82),
                    "selected_source": "cin_postprocess_birth_place_context",
                    "error": None, "review_required": False,
                    "reasons": list(place_field.get("reasons") or []) + [f"birth_place_recovered:{place_reason}"],
                })
                debug["applied"].append({"field": "birth_place", "type": "contextual_place_recovery", "old": old_place, "new": rec_place, "reason": place_reason})
            elif rec_place:
                debug["applied"].append({
                    "field": "birth_place",
                    "type": "postprocess_skip_place_override",
                    "kept": old_place,
                    "candidate": rec_place,
                    "reason": place_reason,
                    "good_spatial": self._field_is_good_spatial(place_field),
                })

        # 5) Do not silently accept suspicious generic birth places.
        place_field = fbname.get("birth_place")
        if place_field and place_field.get("value"):
            place_norm = self._normalize_cin_arabic_text(place_field.get("value"))
            if place_norm in {"الجم"}:
                place_field.update({
                    "validated": False,
                    "confidence": min(float(place_field.get("confidence") or 0.0), 0.60),
                    "error": "Lieu de naissance générique/suspect - vérification requise",
                    "review_required": True,
                    "reasons": list(place_field.get("reasons") or []) + ["invalid_suspicious_birth_place"],
                })
                debug["applied"].append({
                    "field": "birth_place",
                    "type": "invalid_suspicious_birth_place",
                    "value": place_field.get("value"),
                })

        return field_dicts, debug

    # ==================================================================
    # Response builder
    # ==================================================================

    def _detect_language_hint_from_text(self, text: str) -> str:
        return detect_language(normalize_text(text), hint=None) or "unknown"

    def _make_response(
        self, *, job_id: str, request: ExtractionRequest, mode: str,
        quality: Dict[str, Any], transforms: Dict[str, Any], pre_map: Dict[str, Any],
        rois: Dict[str, np.ndarray], trials: List[Dict[str, Any]],
        engine_diags: List[Dict[str, Any]], spatial_debug: Dict[str, Any],
        pipeline_steps: List[str],
    ) -> ExtractionResponse:
        _, fields_api, field_dicts, normalized_data, fusion_debug = self._fuse_and_validate(trials)
        raw_text = self._best_available_raw_text(trials, spatial_debug, engine_diags)
        field_dicts, cin_postprocess_debug = self._apply_cin_postprocess(field_dicts, raw_text)
        normalized_data = self._build_normalized_data(field_dicts)
        quality_score = float((quality or {}).get("quality_score", 0.0) or 0.0)
        business_validation = assess_cin_document(
            field_dicts, quality_score=quality_score,
            success_threshold=getattr(settings, "CIN_SUCCESS_MIN_BUSINESS_CONFIDENCE", 0.88),
        )
        status = business_validation.get("status", "failed")
        global_confidence = float(business_validation.get("business_confidence", 0.0) or 0.0)
        engine_used = self._compute_engine_used_label_from_fields(field_dicts)
        warnings = self._build_warnings_from_fields(field_dicts, quality_score=quality_score)
        diagnostics: Dict[str, Any] = {
            "mode": "cin_specialized_v2", "cin_mode": mode,
            "strategy": (
                "fast: easyocr_boxes -> paddle_boxes only if critical missing -> stop" if mode == "fast"
                else "full: exhaustive spatial + targeted ROI + full OCR" if mode == "full"
                else "balanced: spatial-first + targeted-roi-fallback + optional-full-fallback"
            ),
            "pipeline_steps": pipeline_steps,
            "preprocessing": {"quality": quality, "transforms": transforms, "cin_preprocessor_keys": sorted(pre_map.keys())},
            "quality_checks": quality,
            "roi_fields": {k: list(v.shape[:2]) for k, v in rois.items() if hasattr(v, "shape")},
            "engines": engine_diags, "spatial": spatial_debug,
            "fusion": fusion_debug, "cin_postprocess": cin_postprocess_debug,
        }
        if getattr(request, "metadata", None):
            if routing := request.metadata.get("routing"):
                diagnostics["routing"] = routing
        review_required = bool(business_validation.get("review_required"))
        review_reasons = list(business_validation.get("review_reasons", []))
        diagnostics.update({
            "review_required": review_required, "review_reasons": review_reasons,
            "business_validation": business_validation,
        })
        return ExtractionResponse(
            job_id=job_id, status=status, template_id="cin_tn",
            document_type="cin_tn", document_variant="recto",
            engine_used=engine_used,
            language_detected=self._detect_language_hint_from_text(raw_text),
            global_confidence=global_confidence, quality_score=quality_score,
            fields=field_dicts, normalized_data=normalized_data,
            routing=diagnostics.get("routing"),
            business_validation=business_validation,
            diagnostics=diagnostics if getattr(request, "include_diagnostics", True) else None,
            raw_text=raw_text, processing_time_ms=0,
            warnings=warnings, review_reasons=review_reasons,
        )

    # ==================================================================
    # Main run method
    # ==================================================================

    def run(self, prep: Dict[str, Any], request: ExtractionRequest, job_id: str) -> ExtractionResponse:
        from app.pipeline.cin_field_rois import extract_cin_field_rois
        from app.pipeline.cin_preprocessor import CINPreprocessor

        image, quality, transforms = prep["image"], prep["quality"], prep["transforms"]
        mode = self._resolve_mode(request)

        cin_pre = CINPreprocessor()
        pre_map = cin_pre.preprocess_cin(image) or {}
        card_img = first_non_empty_image(pre_map.get("card_roi"), pre_map.get("oriented"), image)
        if card_img is None:
            raise ValueError("Aucune image CIN exploitable après prétraitement")

        rois = extract_cin_field_rois(card_img) or {}
        language_hints = self._language_hints_for_cin(request)

        engine_diags: List[Dict[str, Any]] = []
        trials: List[Dict[str, Any]] = []
        spatial_debug: Dict[str, Any] = {"trials": []}
        pipeline_steps: List[str] = []

        def _build_response(label: str) -> Tuple[Dict[str, Dict[str, Any]], ExtractionResponse]:
            """
            Build response and derive POST-postprocess validated state.

            FIX v2.1: using _validated_internal_from_field_dicts (postprocessed)
            instead of the pre-postprocess fuse result ensures that corrections
            made by _apply_cin_postprocess are reflected in _can_return_early,
            preventing unnecessary calls to slower engines.
            """
            pipeline_steps.append(label)
            resp = self._make_response(
                job_id=job_id, request=request, mode=mode, quality=quality,
                transforms=transforms, pre_map=pre_map, rois=rois, trials=trials,
                engine_diags=engine_diags, spatial_debug=spatial_debug,
                pipeline_steps=pipeline_steps,
            )
            post_vi = self._validated_internal_from_field_dicts(resp.fields or [])
            return post_vi, resp

        # ── Step 1: easyocr_boxes ─────────────────────────────────────
        if getattr(settings, "ENABLE_EASYOCR", True):
            if trial := self._run_spatial_boxes(
                engine_name="easyocr_boxes", card_img=card_img, spatial_debug=spatial_debug,
            ):
                trials.append(trial)

        post_vi, response = _build_response("easyocr_boxes")
        if self._can_return_early(post_vi, response.business_validation or {}, mode):
            return response

        # ── Step 2: paddle_boxes ──────────────────────────────────────
        paddle_needed = (
            bool(self._missing_critical_fields(post_vi)) if mode == "fast"
            else bool(self._missing_internal_fields(post_vi, mode))
        )
        if getattr(settings, "ENABLE_PADDLE", True) and paddle_needed:
            if trial := self._run_spatial_boxes(
                engine_name="paddle_boxes", card_img=card_img, spatial_debug=spatial_debug,
            ):
                trials.append(trial)
            post_vi, response = _build_response("paddle_boxes")
            if self._can_return_early(post_vi, response.business_validation or {}, mode):
                return response

        # Fast mode ends here
        if mode == "fast":
            return self._make_response(
                job_id=job_id, request=request, mode=mode, quality=quality,
                transforms=transforms, pre_map=pre_map, rois=rois, trials=trials,
                engine_diags=engine_diags, spatial_debug=spatial_debug,
                pipeline_steps=pipeline_steps,
            )

        # ── Step 3: targeted ROI OCR ──────────────────────────────────
        missing = self._missing_internal_fields(post_vi, mode)
        if missing:
            for engine_name in list(getattr(settings, "CIN_BALANCED_TARGETED_ENGINES", ["easyocr", "paddle"])):
                eng_diag, trial = self._run_targeted_roi_trial(
                    engine_name=engine_name, missing_fields=missing,
                    rois=rois, language_hints=language_hints,
                )
                engine_diags.append(eng_diag)
                if trial:
                    trials.append(trial)
                post_vi, response = _build_response(f"targeted_roi:{engine_name}")
                missing = self._missing_internal_fields(post_vi, mode)
                if self._can_return_early(post_vi, response.business_validation or {}, mode):
                    return response
                if not missing:
                    break

        # ── Step 4: full fallback ─────────────────────────────────────
        missing = self._missing_internal_fields(post_vi, mode)
        if mode == "full" or (mode == "balanced" and missing
                              and bool(getattr(settings, "CIN_BALANCED_ALLOW_FULL_FALLBACK", True))):
            for engine_name in self._select_cin_engines(request, mode="full"):
                eng_diag, trial = self._run_cin_engine_trial(
                    engine_name=engine_name, card_img=card_img,
                    rois=rois, language_hints=language_hints,
                )
                engine_diags.append(eng_diag)
                if trial:
                    trials.append(trial)
                if mode == "balanced":
                    post_vi, response = _build_response(f"full_ocr:{engine_name}")
                    if self._can_return_early(post_vi, response.business_validation or {}, mode):
                        return response
                else:
                    pipeline_steps.append(f"full_ocr:{engine_name}")

        return self._make_response(
            job_id=job_id, request=request, mode=mode, quality=quality,
            transforms=transforms, pre_map=pre_map, rois=rois, trials=trials,
            engine_diags=engine_diags, spatial_debug=spatial_debug,
            pipeline_steps=pipeline_steps,
        )
