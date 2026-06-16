#cin_spatial_extractor.py
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.cin_rules import (
    clean_words,
    contains_label_fragment,
    contains_relation_word,
    digits_only,
    extract_best_birth_date_from_text,
    extract_best_birth_place_from_text,
    is_valid_birth_place_strict,
    is_valid_cin_number,
    is_valid_family_name,
    is_valid_given_name,
    normalize_name,
    normalize_place,
    parse_date_any,
)

FAMILY_LABELS = ["اللقب", "الاقب", "للقب", "لقب", "بقللا", "اللفب", "للفب", "القب"]
FIRST_LABELS = ["الاسم", "الإسم", "الام", "اام", "مسالا", "اللسم", "السم", "اسم", "الم"]
DATE_LABELS = ["تاريخ الولادة", "تاريخ", "الولادة", "تاخ", "خات", "غرات", "تارخ", "اخ"]
PLACE_LABELS = ["مكانها", "مكان الولادة", "مكان", "محل", "اهناكم", "اهام", "تهاها", "اهزاكم", "عانا", "كانها"]

HEADER_WORDS = {"الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية"}


@dataclass
class OCRBox:
    text: str
    confidence: float
    bbox: List[List[float]]
    engine: str
    x1: float
    y1: float
    x2: float
    y2: float
    cx: float
    cy: float
    w: float
    h: float
    idx: int


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return default if x is None else float(x)
    except Exception:
        return default


def _flatten_bbox(bbox: List[List[float]]) -> Tuple[float, float, float, float]:
    xs = [float(p[0]) for p in bbox]
    ys = [float(p[1]) for p in bbox]
    return min(xs), min(ys), max(xs), max(ys)


def _to_box(item: Dict[str, Any], idx: int) -> OCRBox:
    x1, y1, x2, y2 = _flatten_bbox(item["bbox"])
    return OCRBox(
        text=str(item.get("text", "")).strip(),
        confidence=_safe_float(item.get("confidence"), 0.0),
        bbox=item["bbox"],
        engine=str(item.get("engine", "")),
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        cx=(x1 + x2) / 2.0,
        cy=(y1 + y2) / 2.0,
        w=max(1.0, x2 - x1),
        h=max(1.0, y2 - y1),
        idx=idx,
    )


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _normalize_for_match(text: str) -> str:
    t = _clean_text(text)
    t = t.replace("إ", "ا").replace("أ", "ا").replace("آ", "ا")
    t = t.replace("ة", "ه").replace("ى", "ي")
    return t


def _token_matches_label(token: str, labels: List[str]) -> bool:
    tok = _normalize_for_match(token)
    rev = tok[::-1]
    for lab in labels:
        nlab = _normalize_for_match(lab)
        if tok == nlab or rev == nlab:
            return True
    return False


def _text_has_label(text: str, labels: List[str]) -> bool:
    words = clean_words(text)
    for w in words:
        if _token_matches_label(w, labels):
            return True

    compact = _normalize_for_match(_clean_text(text)).replace(" ", "")
    for lab in labels:
        nlab = _normalize_for_match(lab).replace(" ", "")
        if nlab in compact or nlab in compact[::-1]:
            return True
    return False


def _strip_label_from_text(text: str, labels: List[str]) -> str:
    t = _clean_text(text)
    for lab in sorted(labels, key=len, reverse=True):
        t = t.replace(lab, " ")
        t = t.replace(lab[::-1], " ")
    t = re.sub(r"[:؛،,\-_/|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_headerish(text: str) -> bool:
    return any(w in HEADER_WORDS for w in clean_words(text))


def _same_row(a: OCRBox, b: OCRBox) -> bool:
    return abs(a.cy - b.cy) <= max(a.h, b.h) * 0.90


def _near_below(a: OCRBox, b: OCRBox) -> bool:
    return b.cy > a.cy and abs(b.cy - a.cy) <= max(a.h, b.h) * 2.4


def _near_above(a: OCRBox, b: OCRBox) -> bool:
    return b.cy < a.cy and abs(b.cy - a.cy) <= max(a.h, b.h) * 2.0


def _euclidean(a: OCRBox, b: OCRBox) -> float:
    return math.hypot(a.cx - b.cx, a.cy - b.cy)


def _merge_same_row_neighbor_text(anchor: OCRBox, neighbor: OCRBox) -> str:
    if neighbor.x1 < anchor.x1:
        return f"{neighbor.text} {anchor.text}"
    return f"{anchor.text} {neighbor.text}"


def _build_boxes(raw_boxes: List[Dict[str, Any]]) -> List[OCRBox]:
    boxes = [_to_box(item, i) for i, item in enumerate(raw_boxes or []) if str(item.get("text", "")).strip()]
    boxes.sort(key=lambda b: (b.cy, b.cx))
    return boxes


def _card_width(boxes: List[OCRBox]) -> float:
    return max((b.x2 for b in boxes), default=1.0)


def _right_side_boxes(boxes: List[OCRBox]) -> List[OCRBox]:
    width = _card_width(boxes)
    threshold = width * 0.42
    right_boxes = [b for b in boxes if b.x1 >= threshold or b.cx >= threshold]
    right_boxes.sort(key=lambda b: (b.cy, b.x1))
    return right_boxes


def _boxes_to_text(boxes: List[OCRBox]) -> str:
    ordered = sorted(boxes, key=lambda b: (b.cy, b.x1))
    return _clean_text(" ".join(b.text for b in ordered if b.text))


def _row_context_text(anchor: OCRBox, boxes: List[OCRBox]) -> str:
    related = []
    for b in boxes:
        if b.idx == anchor.idx:
            related.append(b)
            continue
        if _same_row(anchor, b) or _near_below(anchor, b):
            related.append(b)
    related.sort(key=lambda b: (b.cy, b.x1))
    return _boxes_to_text(related)


def _candidate_from_box_text(text: str, validator) -> Optional[str]:
    raw = _clean_text(text)
    if not raw:
        return None
    if contains_label_fragment(raw):
        return None
    if contains_relation_word(raw):
        return None
    return raw if validator(raw) else None


def _best_numeric_line_candidate(boxes: List[OCRBox]) -> Tuple[Optional[str], float, Dict[str, Any]]:
    best = None
    best_score = -1.0
    debug = {"chosen_from": None, "candidate_text": None, "source_kind": "spatial_numeric"}

    for b in boxes:
        d = digits_only(b.text)
        m = re.search(r"\d{8}", d)
        if not m:
            continue
        value = m.group(0)
        if not is_valid_cin_number(value):
            continue
        score = 20.0 + 0.2 * b.confidence
        if score > best_score:
            best = value
            best_score = score
            debug = {"chosen_from": value, "candidate_text": b.text, "source_kind": "spatial_numeric"}

    return best, best_score, debug


def _extract_name_field(
    boxes: List[OCRBox],
    labels: List[str],
    *,
    validator,
) -> Tuple[Optional[str], float, Dict[str, Any]]:
    anchors = [b for b in boxes if _text_has_label(b.text, labels)]
    if not anchors:
        return None, -1.0, {"chosen_from": None, "source_kind": "no_label", "anchors_found": 0}

    candidates: List[Tuple[str, float, Dict[str, Any]]] = []

    for anchor in anchors:
        inline = _strip_label_from_text(anchor.text, labels)
        inline_val = _candidate_from_box_text(inline, validator)
        if inline_val:
            candidates.append(
                (
                    inline_val,
                    15.0 + 0.20 * anchor.confidence,
                    {
                        "chosen_from": anchor.text,
                        "candidate_text": inline,
                        "source_kind": "inline",
                        "anchors_found": len(anchors),
                    },
                )
            )

        for nb in boxes:
            if nb.idx == anchor.idx:
                continue
            if _is_headerish(nb.text):
                continue
            dist = _euclidean(anchor, nb)

            if _same_row(anchor, nb):
                direct_val = _candidate_from_box_text(nb.text, validator)
                if direct_val:
                    candidates.append(
                        (
                            direct_val,
                            12.0 + 0.18 * nb.confidence - 0.02 * dist,
                            {
                                "chosen_from": anchor.text,
                                "candidate_text": nb.text,
                                "source_kind": "label_neighbor",
                                "anchors_found": len(anchors),
                            },
                        )
                    )

                merged = _strip_label_from_text(_merge_same_row_neighbor_text(anchor, nb), labels)
                merged_val = _candidate_from_box_text(merged, validator)
                if merged_val:
                    candidates.append(
                        (
                            merged_val,
                            12.5 + 0.18 * nb.confidence - 0.02 * dist,
                            {
                                "chosen_from": anchor.text,
                                "candidate_text": merged,
                                "source_kind": "label_neighbor_merged",
                                "anchors_found": len(anchors),
                            },
                        )
                    )

            elif _near_below(anchor, nb) or _near_above(anchor, nb):
                direct_val = _candidate_from_box_text(nb.text, validator)
                if direct_val:
                    candidates.append(
                        (
                            direct_val,
                            8.5 + 0.15 * nb.confidence - 0.02 * dist,
                            {
                                "chosen_from": anchor.text,
                                "candidate_text": nb.text,
                                "source_kind": "below_anchor",
                                "anchors_found": len(anchors),
                            },
                        )
                    )

    if not candidates:
        return None, -1.0, {"chosen_from": None, "source_kind": "no_valid_neighbor", "anchors_found": len(anchors)}

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def _extract_date_field(
    boxes: List[OCRBox],
    labels: List[str],
) -> Tuple[Optional[str], float, Dict[str, Any]]:
    anchors = [b for b in boxes if _text_has_label(b.text, labels)]
    candidates: List[Tuple[str, float, Dict[str, Any]]] = []

    for anchor in anchors:
        inline = _strip_label_from_text(anchor.text, labels)
        iso = parse_date_any(inline)
        if iso:
            candidates.append(
                (
                    iso,
                    14.0 + 0.20 * anchor.confidence,
                    {
                        "chosen_from": anchor.text,
                        "candidate_text": inline,
                        "source_kind": "inline",
                        "anchors_found": len(anchors),
                    },
                )
            )

        joined = _strip_label_from_text(_row_context_text(anchor, boxes), labels)
        iso = parse_date_any(joined)
        if iso:
            candidates.append(
                (
                    iso,
                    12.5,
                    {
                        "chosen_from": anchor.text,
                        "candidate_text": joined,
                        "source_kind": "label_row_join",
                        "anchors_found": len(anchors),
                    },
                )
            )

    right_text = _boxes_to_text(_right_side_boxes(boxes))
    date_fb = extract_best_birth_date_from_text(right_text)
    if date_fb:
        candidates.append(
            (
                date_fb,
                9.5,
                {
                    "chosen_from": "right_text_block",
                    "candidate_text": right_text,
                    "source_kind": "right_text_fallback",
                    "anchors_found": len(anchors),
                },
            )
        )

    if not candidates:
        return None, -1.0, {"chosen_from": None, "source_kind": "no_valid_date", "anchors_found": len(anchors)}

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


def _extract_place_field(
    boxes: List[OCRBox],
    labels: List[str],
) -> Tuple[Optional[str], float, Dict[str, Any]]:
    anchors = [b for b in boxes if _text_has_label(b.text, labels)]
    candidates: List[Tuple[str, float, Dict[str, Any]]] = []

    for anchor in anchors:
        inline = _strip_label_from_text(anchor.text, labels)
        place = normalize_place(inline)
        if place and is_valid_birth_place_strict(place):
            candidates.append(
                (
                    place,
                    14.0 + 0.20 * anchor.confidence,
                    {
                        "chosen_from": anchor.text,
                        "candidate_text": inline,
                        "source_kind": "inline",
                        "anchors_found": len(anchors),
                    },
                )
            )

        joined = _strip_label_from_text(_row_context_text(anchor, boxes), labels)
        place = extract_best_birth_place_from_text(joined)
        if place and is_valid_birth_place_strict(place):
            candidates.append(
                (
                    place,
                    11.5,
                    {
                        "chosen_from": anchor.text,
                        "candidate_text": joined,
                        "source_kind": "label_row_join",
                        "anchors_found": len(anchors),
                    },
                )
            )

    right_text = _boxes_to_text(_right_side_boxes(boxes))
    place_fb = extract_best_birth_place_from_text(right_text)
    if place_fb and is_valid_birth_place_strict(place_fb):
        candidates.append(
            (
                place_fb,
                9.0,
                {
                    "chosen_from": "right_text_block",
                    "candidate_text": right_text,
                    "source_kind": "right_text_fallback",
                    "anchors_found": len(anchors),
                },
            )
        )

    if not candidates:
        return None, -1.0, {"chosen_from": None, "source_kind": "no_valid_place", "anchors_found": len(anchors)}

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


class CINSpatialExtractor:
    def extract(self, raw_boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
        boxes = _build_boxes(raw_boxes)

        fields: Dict[str, Any] = {}
        scores: Dict[str, float] = {}
        meta: Dict[str, Any] = {}

        cin_number, cin_score, cin_meta = _best_numeric_line_candidate(boxes)
        if cin_number:
            fields["cin_number"] = cin_number
            scores["cin_number"] = cin_score
        meta["cin_number"] = cin_meta

        fam, fam_score, fam_meta = _extract_name_field(
            boxes,
            FAMILY_LABELS,
            validator=is_valid_family_name,
        )
        if fam:
            fam_norm = normalize_name(fam)
            if fam_norm and not contains_label_fragment(fam_norm):
                fields["family_name"] = fam_norm
                scores["family_name"] = fam_score
        meta["family_name"] = fam_meta

        fst, fst_score, fst_meta = _extract_name_field(
            boxes,
            FIRST_LABELS,
            validator=is_valid_given_name,
        )
        if fst:
            fst_norm = normalize_name(fst)
            if fst_norm and not contains_label_fragment(fst_norm):
                fields["first_name"] = fst_norm
                scores["first_name"] = fst_score
        meta["first_name"] = fst_meta

        dob, dob_score, dob_meta = _extract_date_field(boxes, DATE_LABELS)
        if dob:
            fields["date_of_birth"] = dob
            scores["date_of_birth"] = dob_score
        meta["date_of_birth"] = dob_meta

        plc, plc_score, plc_meta = _extract_place_field(boxes, PLACE_LABELS)
        if plc:
            fields["place_of_birth"] = normalize_place(plc)
            scores["place_of_birth"] = plc_score
        meta["place_of_birth"] = plc_meta

        if fields.get("family_name") and fields.get("first_name"):
            if normalize_name(fields["family_name"]) == normalize_name(fields["first_name"]):
                fields.pop("first_name", None)
                scores.pop("first_name", None)
                meta["first_name"] = {"chosen_from": None, "source_kind": "dropped_duplicate"}

        return {
            "fields": fields,
            "scores": scores,
            "meta": meta,
            "box_count": len(boxes),
        }