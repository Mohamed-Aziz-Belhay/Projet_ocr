"""
app/services/cin_label_spatial_extractor.py
Extract fields from OCR boxes using local label context.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from app.pipeline.common import safe_float
from app.services.cin_text_normalizer import CINTextNormalizer
from app.services.cin_field_parsers import CINFieldParsers


class CINLabelSpatialExtractor:
    def __init__(
        self,
        normalizer: Optional[CINTextNormalizer] = None,
        parsers: Optional[CINFieldParsers] = None,
    ):
        self.norm    = normalizer or CINTextNormalizer()
        self.parsers = parsers or CINFieldParsers(self.norm)

    def _box_text_conf_bbox(self, box: Any) -> Tuple[str, float, Optional[Any]]:
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

    def _items_from_boxes(self, raw_boxes: Any) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for i, box in enumerate(raw_boxes or []):
            text, conf, bbox = self._box_text_conf_bbox(box)
            if not text:
                continue
            geom = self._bbox_metrics(bbox)
            if not geom:
                continue
            items.append({"i": i, "text": text, "norm": self.norm.normalize_arabic_text(text), "conf": conf, **geom})
        return items

    def _cluster_items_into_lines(self, items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        if not items:
            return []
        ordered = sorted(items, key=lambda it: (it["cy"], it["x1"]))
        median_h = float(np.median([it["h"] for it in ordered] or [20.0]))
        y_tol = max(12.0, median_h * 0.75)

        lines: List[List[Dict[str, Any]]] = []
        for item in ordered:
            placed = False
            for line in lines:
                line_cy = sum(x["cy"] for x in line) / max(1, len(line))
                if abs(item["cy"] - line_cy) <= y_tol:
                    line.append(item)
                    placed = True
                    break
            if not placed:
                lines.append([item])

        for line in lines:
            line.sort(key=lambda it: it["x1"])
        lines.sort(key=lambda line: sum(it["cy"] for it in line) / max(1, len(line)))
        return lines

    def _line_text_variants(self, line: List[Dict[str, Any]]) -> List[str]:
        asc  = " ".join(it["norm"] for it in sorted(line, key=lambda it: it["x1"]) if it.get("norm"))
        desc = " ".join(it["norm"] for it in sorted(line, key=lambda it: it["x1"], reverse=True) if it.get("norm"))
        raw  = " ".join(it["norm"] for it in line if it.get("norm"))
        variants: List[str] = []
        for value in (raw, asc, desc):
            value = re.sub(r"\s+", " ", value).strip()
            if value and value not in variants:
                variants.append(value)
        return variants

    def _text_has_label(self, text: str, label: str) -> bool:
        norm    = self.norm.normalize_arabic_text(text)
        compact = re.sub(r"\s+", "", norm)
        if label == "family_name":
            return "اللقب" in norm or "اللقب" in compact
        if label == "first_name":
            return "الاسم" in norm or "الاسم" in compact
        if label == "birth_place":
            return "موامه" in norm or "موامه" in compact or "مولد" in norm
        if label == "birth_date":
            return "تاريخ" in norm or "الولادة" in norm
        return False

    def _contexts_around_label(self, lines: List[List[Dict[str, Any]]], label: str, max_extra_lines: int = 1) -> List[str]:
        contexts: List[str] = []
        for idx, line in enumerate(lines):
            variants = self._line_text_variants(line)
            if not any(self._text_has_label(v, label) for v in variants):
                continue
            contexts.extend(variants)
            if max_extra_lines > 0 and idx + 1 < len(lines):
                for v in variants:
                    for nv in self._line_text_variants(lines[idx + 1]):
                        combo = re.sub(r"\s+", " ", f"{v} {nv}").strip()
                        if combo and combo not in contexts:
                            contexts.append(combo)
            if idx > 0:
                for pv in self._line_text_variants(lines[idx - 1]):
                    for v in variants:
                        combo = re.sub(r"\s+", " ", f"{pv} {v}").strip()
                        if combo and combo not in contexts:
                            contexts.append(combo)
        return contexts

    def extract(self, raw_boxes: Any) -> Dict[str, Any]:
        items = self._items_from_boxes(raw_boxes)
        if not items:
            return {"fields": {}, "scores": {}, "meta": {"reason": "no_items"}}

        lines   = self._cluster_items_into_lines(items)
        fields:  Dict[str, Any]   = {}
        scores:  Dict[str, float] = {}
        meta:    Dict[str, Any]   = {"line_count": len(lines), "candidates": {}}

        # Family name
        family_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._contexts_around_label(lines, "family_name", max_extra_lines=1):
            value = self.parsers.extract_family_name_from_text(ctx)
            if value and self.parsers.looks_like_family_name(value):
                family_candidates.append((50 + (10 if "اللقب" in ctx else 0), value, ctx))
        if family_candidates:
            best = max(family_candidates, key=lambda x: x[0])
            fields["family_name"] = best[1]
            scores["family_name"] = 0.94
            meta["candidates"]["family_name"] = family_candidates[:10]

        # First name
        first_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._contexts_around_label(lines, "first_name", max_extra_lines=1):
            value = self.parsers.extract_first_name_from_text(ctx)
            if value and self.parsers.looks_like_first_name_phrase(value):
                first_candidates.append((50 + (10 if "الاسم" in ctx else 0), value, ctx))
        if first_candidates:
            best = max(first_candidates, key=lambda x: x[0])
            fields["first_name"] = best[1]
            scores["first_name"] = 0.94
            meta["candidates"]["first_name"] = first_candidates[:10]

        # Birth date
        date_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._contexts_around_label(lines, "birth_date", max_extra_lines=1):
            value, reason = self.parsers.extract_birth_date_from_context(ctx)
            if value:
                date_candidates.append((45 + (5 if reason else 0), value, ctx))
        if date_candidates:
            best = max(date_candidates, key=lambda x: x[0])
            fields["date_of_birth"] = best[1]
            scores["date_of_birth"] = 0.90
            meta["candidates"]["date_of_birth"] = date_candidates[:10]

        # Birth place
        place_candidates: List[Tuple[int, str, str]] = []
        for ctx in self._contexts_around_label(lines, "birth_place", max_extra_lines=0):
            value, reason = self.parsers.extract_birth_place_from_context(ctx)
            if value:
                place_candidates.append((45 + (8 if reason and reason.startswith("place_after_label") else 0), value, ctx))
        if place_candidates:
            best = max(place_candidates, key=lambda x: x[0])
            fields["place_of_birth"] = best[1]
            scores["place_of_birth"] = 0.93
            meta["candidates"]["place_of_birth"] = place_candidates[:10]

        return {"fields": fields, "scores": scores, "meta": meta}