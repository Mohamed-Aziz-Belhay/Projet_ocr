
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.cin_rules import (
    digits_only,
    extract_best_birth_date_from_text,
    is_placeholder_value,
    parse_date_any,
    normalize_name,
)
from app.services.cin_text_parser import (
    best_known_place_from_text,
    extract_name_pair_from_text,
    repair_name_candidate,
    repair_place_candidate,
)
from app.services.cin_candidate_ranker import (
    aggregate_candidates,
    find_candidate,
    first_ranked_from_sources,
    select_best_place,
)


def _trial_get(trial: Any, key: str, default=None):
    if isinstance(trial, dict):
        return trial.get(key, default)
    return getattr(trial, key, default)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return default if x is None else float(x)
    except Exception:
        return default


def _transport_date_value(iso_or_text: str) -> Optional[str]:
    raw = str(iso_or_text or "").strip()
    if not raw:
        return None

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return f"{d}/{mo}/{y}"

    try:
        iso = parse_date_any(raw)
    except Exception:
        iso = None

    if not iso:
        return None

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso)
    if not m:
        return None

    y, mo, d = m.group(1), m.group(2), m.group(3)
    return f"{d}/{mo}/{y}"


def _normalize_candidate(field: str, value: Any) -> Optional[str]:
    if value is None:
        return None

    raw = str(value).strip()
    if not raw or is_placeholder_value(raw):
        return None

    if field == "cin_number":
        d = digits_only(raw)
        return d if re.fullmatch(r"\d{8}", d) else None

    if field == "date_of_birth":
        return _transport_date_value(raw)

    if field == "family_name":
        return repair_name_candidate(raw, validator=lambda x: True)

    if field == "first_name":
        return repair_name_candidate(raw, validator=lambda x: True)

    if field == "place_of_birth":
        return repair_place_candidate(raw)

    return None


def _add_candidate(
    bag: Dict[str, List[Dict[str, Any]]],
    field: str,
    value: Any,
    trial: Any,
    source: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return

    norm = _normalize_candidate(field, raw)
    if not norm:
        return

    bag[field].append(
        {
            "value": norm,
            "raw_value": raw,
            "source": source,
            "engine": _trial_get(trial, "engine"),
            "profile": _trial_get(trial, "profile"),
            "avg_conf": _safe_float(_trial_get(trial, "avg_conf", _trial_get(trial, "score", 0.0)), 0.0),
            "roi_score": _safe_float(_trial_get(trial, "roi_score", _trial_get(trial, "score", 0.0)), 0.0),
            "meta": meta or {},
        }
    )


def _combined_raw_text(trials: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    seen: Set[str] = set()
    for tr in trials or []:
        raw = str(tr.get("raw_text") or "").strip()
        if raw and raw not in seen:
            parts.append(raw)
            seen.add(raw)
    return "\n".join(parts)


class CINFieldFuser:
    FIELDS = ["cin_number", "family_name", "first_name", "date_of_birth", "place_of_birth"]

    def fuse(self, adapter: Any, trials: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        bag: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        debug_info: Dict[str, Any] = {
            "trials": [],
            "aggregated": {},
            "selected": {},
        }

        for tr in trials or []:
            raw_text = tr.get("raw_text") or ""
            extracted = dict(tr.get("extracted") or {})

            for field in self.FIELDS:
                if field in extracted and extracted[field] not in (None, "", []):
                    _add_candidate(
                        bag,
                        field,
                        extracted[field],
                        tr,
                        source=str(tr.get("source") or "extractor"),
                        meta=tr.get("meta") if isinstance(tr.get("meta"), dict) else None,
                    )

            if raw_text:
                try:
                    parsed = adapter.extract_fields(raw_text, trials=trials) or {}
                except Exception:
                    parsed = {}

                for field in self.FIELDS:
                    value = parsed.get(field)
                    if value in (None, "", []):
                        continue
                    _add_candidate(
                        bag,
                        field,
                        value,
                        tr,
                        source="extractor",
                        meta={"from_raw_text": True},
                    )

                family_fb, first_fb = extract_name_pair_from_text(raw_text)

                if family_fb:
                    _add_candidate(
                        bag,
                        "family_name",
                        family_fb,
                        tr,
                        source="text_family_fallback",
                        meta={"from_raw_text": True},
                    )

                if first_fb:
                    _add_candidate(
                        bag,
                        "first_name",
                        first_fb,
                        tr,
                        source="text_first_fallback",
                        meta={"from_raw_text": True},
                    )

                date_fb = extract_best_birth_date_from_text(raw_text)
                if date_fb:
                    _add_candidate(
                        bag,
                        "date_of_birth",
                        date_fb,
                        tr,
                        source="text_date_fallback",
                        meta={"from_raw_text": True},
                    )

                place_fb = best_known_place_from_text(raw_text)
                if place_fb:
                    _add_candidate(
                        bag,
                        "place_of_birth",
                        place_fb,
                        tr,
                        source="text_place_fallback",
                        meta={"from_raw_text": True},
                    )

            debug_info["trials"].append(
                {
                    "engine": tr.get("engine"),
                    "source": tr.get("source"),
                    "fields": extracted,
                }
            )

        combined_text = _combined_raw_text(trials)
        if combined_text:
            family_global, first_global = extract_name_pair_from_text(combined_text)

            if family_global:
                _add_candidate(
                    bag,
                    "family_name",
                    family_global,
                    {"engine": "global_text", "score": 0.0},
                    source="text_family_global",
                    meta={"from_combined_text": True},
                )

            if first_global:
                _add_candidate(
                    bag,
                    "first_name",
                    first_global,
                    {"engine": "global_text", "score": 0.0},
                    source="text_first_global",
                    meta={"from_combined_text": True},
                )

            place_global = best_known_place_from_text(combined_text)
            if place_global:
                _add_candidate(
                    bag,
                    "place_of_birth",
                    place_global,
                    {"engine": "global_text", "score": 0.0},
                    source="text_place_global",
                    meta={"from_combined_text": True},
                )

        aggregated: Dict[str, List[Dict[str, Any]]] = {}
        for field in self.FIELDS:
            aggregated[field] = aggregate_candidates(field, bag.get(field, []))
            debug_info["aggregated"][field] = aggregated[field]

        selected: Dict[str, Any] = {}

        cin_ranked = aggregated["cin_number"]
        if cin_ranked:
            selected["cin_number"] = cin_ranked[0]["value"]
            debug_info["selected"]["cin_number"] = cin_ranked[0]

        family_ranked = aggregated["family_name"]
        first_ranked = aggregated["first_name"]

        family_spatial = first_ranked_from_sources(
            family_ranked,
            {"spatial_boxes", "label_neighbor", "label_neighbor_merged"},
            min_score=20.0,
            min_chars=3,
        )
        first_spatial = first_ranked_from_sources(
            first_ranked,
            {"spatial_boxes", "label_neighbor", "label_neighbor_merged"},
            min_score=20.0,
            min_chars=3,
            forbidden_values={family_spatial} if family_spatial else set(),
        )

        family_text = first_ranked_from_sources(
            family_ranked,
            {"text_family_global", "text_family_fallback"},
            min_score=22.0,
            min_chars=3,
        )
        first_text = first_ranked_from_sources(
            first_ranked,
            {"text_first_global", "text_first_fallback"},
            min_score=22.0,
            min_chars=3,
            forbidden_values={family_text} if family_text else set(),
        )

        family_choice = family_spatial or family_text
        first_choice = first_spatial or first_text

        fam_text_cand = find_candidate(family_ranked, family_text)
        fst_text_cand = find_candidate(first_ranked, first_text)
        fam_spat_cand = find_candidate(family_ranked, family_spatial)
        fst_spat_cand = find_candidate(first_ranked, first_spatial)

        if family_text and first_text and family_text != first_text:
            text_pair_score = (fam_text_cand or {}).get("selection_score", 0.0) + (fst_text_cand or {}).get("selection_score", 0.0)
            spat_pair_score = (fam_spat_cand or {}).get("selection_score", 0.0) + (fst_spat_cand or {}).get("selection_score", 0.0)

            if (
                not family_spatial
                or not first_spatial
                or family_spatial == first_text
                or first_spatial == family_text
                or text_pair_score >= spat_pair_score - 1.0
            ):
                family_choice = family_text
                first_choice = first_text

        if first_choice:
            chosen_first = find_candidate(first_ranked, first_choice)
            if chosen_first:
                sources = set(chosen_first.get("sources") or [])
                if sources <= {"text_first_fallback"} and chosen_first.get("selection_score", 0.0) < 31.0:
                    first_choice = first_spatial if first_spatial and first_spatial != family_choice else None

        if family_choice:
            selected["family_name"] = normalize_name(family_choice)
        if first_choice:
            selected["first_name"] = normalize_name(first_choice)

        if selected.get("family_name") == selected.get("first_name"):
            selected.pop("first_name", None)

        debug_info["selected"]["family_name"] = find_candidate(family_ranked, selected.get("family_name"))
        debug_info["selected"]["first_name"] = find_candidate(first_ranked, selected.get("first_name"))

        date_ranked = aggregated["date_of_birth"]
        date_choice = first_ranked_from_sources(
            date_ranked,
            {"spatial_boxes", "text_date_fallback", "label_row_join", "right_text_fallback"},
            min_score=10.0,
            min_chars=1,
        )

        if not date_choice and date_ranked:
            for cand in date_ranked:
                if cand["selection_score"] >= 10.0:
                    date_choice = cand["value"]
                    break

        if date_choice:
            selected["date_of_birth"] = date_choice

        debug_info["selected"]["date_of_birth"] = find_candidate(date_ranked, selected.get("date_of_birth"))

        place_ranked = aggregated["place_of_birth"]
        place_choice = select_best_place(place_ranked)
        if place_choice:
            selected["place_of_birth"] = place_choice

        debug_info["selected"]["place_of_birth"] = next(
            (c for c in place_ranked if c["value"] == selected.get("place_of_birth")),
            None,
        )

        return dict(selected), debug_info