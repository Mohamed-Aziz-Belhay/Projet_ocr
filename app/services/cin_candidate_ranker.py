from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.cin_text_parser import (
    all_known_places,
    canonicalize_place,
    name_char_len,
    name_plausibility_score,
)


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return default if x is None else float(x)
    except Exception:
        return default


def profile_bonus(profile: str) -> float:
    p = (profile or "").strip().lower()
    if p == "cin_roi":
        return 3.0
    if p == "scan_dense":
        return 2.4
    if p == "receipt":
        return 1.7
    if p == "id_card":
        return 1.0
    return 0.0


def source_bonus(field: str, source: str) -> float:
    table = {
        "extractor": 0.5,
        "global_scan": 0.6,
        "text_date_fallback": 12.0,
        "text_place_fallback": 16.0,
        "text_family_fallback": 20.0,
        "text_first_fallback": 20.0,
        "text_family_global": 26.0,
        "text_first_global": 26.0,
        "text_place_global": 28.0,
        "label_family": 20.0,
        "label_family_nextline": 18.0,
        "label_family_prevline": 20.0,
        "label_first": 20.0,
        "label_first_nextline": 18.0,
        "label_first_prevline": 20.0,
        "position_family": 8.0,
        "explicit_place": 18.0,
        "roi_family_marker": 10.0,
        "roi_first_marker": 10.0,
        "roi_place_marker": 9.0,
        "tail_place": 9.0,
        "spatial_boxes": 22.0,
        "label_neighbor": 18.0,
        "label_neighbor_merged": 16.0,
        "below_anchor": 8.0,
        "label_row_join": 16.0,
        "right_text_fallback": 14.0,
    }
    if field == "cin_number" and source == "extractor":
        return 6.0
    if field == "date_of_birth" and source == "extractor":
        return 4.0
    return table.get(source, 0.0)


def aggregate_candidates(field: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for c in candidates:
        key = str(c["value"])
        g = grouped.setdefault(
            key,
            {
                "value": c["value"],
                "avg_conf_sum": 0.0,
                "count": 0,
                "engines": set(),
                "sources": set(),
                "profiles": set(),
                "raw_values": set(),
                "meta": [],
            },
        )

        g["count"] += 1
        g["avg_conf_sum"] += _safe_float(c.get("avg_conf"), 0.0)
        if c.get("engine"):
            g["engines"].add(c["engine"])
        if c.get("source"):
            g["sources"].add(c["source"])
        if c.get("profile"):
            g["profiles"].add(c["profile"])
        if c.get("raw_value"):
            g["raw_values"].add(c["raw_value"])
        if c.get("meta"):
            g["meta"].append(c["meta"])

    out: List[Dict[str, Any]] = []

    for g in grouped.values():
        avg_conf = g["avg_conf_sum"] / max(g["count"], 1)
        sources = sorted(g["sources"])
        profiles = sorted(g["profiles"])

        sb = max((source_bonus(field, s) for s in sources), default=0.0)
        pb = max((profile_bonus(p) for p in profiles), default=0.0)
        selection_score = (g["count"] * 7.0) + avg_conf + sb + pb

        if field in {"family_name", "first_name"}:
            selection_score += name_plausibility_score(g["value"])
            if name_char_len(g["value"]) < 3:
                selection_score -= 10.0
            raw_values = list(g["raw_values"])
            if raw_values and any(rv == g["value"] for rv in raw_values):
                selection_score += 2.0

        if field == "place_of_birth":
            place_norm = canonicalize_place(g["value"])
            if place_norm in all_known_places():
                selection_score += 10.0 + 3.0 * len(place_norm.split())
            else:
                selection_score -= 8.0
            if set(sources) <= {"extractor", "roi_ocr"}:
                selection_score -= 12.0

        out.append(
            {
                "value": g["value"],
                "avg_conf": round(avg_conf, 6),
                "count": g["count"],
                "engines": sorted(g["engines"]),
                "sources": sources,
                "profiles": profiles,
                "selection_score": round(selection_score, 6),
                "raw_value": next(iter(g["raw_values"])) if g["raw_values"] else g["value"],
                "meta": g["meta"],
            }
        )

    out.sort(key=lambda x: (x["selection_score"], x["count"], x["avg_conf"]), reverse=True)
    return out


def find_candidate(ranked: List[Dict[str, Any]], value: Optional[str]) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    return next((c for c in ranked if c["value"] == value), None)


def first_ranked_from_sources(
    ranked: List[Dict[str, Any]],
    allowed_sources: Set[str],
    *,
    min_score: float = 0.0,
    min_chars: int = 3,
    forbidden_values: Optional[Set[str]] = None,
) -> Optional[str]:
    forbidden_values = forbidden_values or set()
    known_places = all_known_places()

    for cand in ranked:
        if cand["value"] in forbidden_values:
            continue
        if cand.get("selection_score", 0.0) < min_score:
            continue
        if name_char_len(cand["value"]) < min_chars and cand["value"] not in known_places:
            continue
        if any(src in allowed_sources for src in cand.get("sources", [])):
            return cand["value"]
    return None


def select_best_place(place_ranked: List[Dict[str, Any]]) -> Optional[str]:
    if not place_ranked:
        return None

    values = [canonicalize_place(c["value"]) for c in place_ranked]
    known: List[Tuple[float, str]] = []
    known_places = all_known_places()

    for cand in place_ranked:
        value = canonicalize_place(cand["value"])
        sources = set(cand.get("sources") or [])
        if value not in known_places:
            continue

        score = cand.get("selection_score", 0.0)
        if "text_place_global" in sources:
            score += 8.0
        if "spatial_boxes" in sources:
            score += 4.0
        if len(value.split()) >= 2:
            score += 6.0

        if any(other != value and value in other for other in values):
            score -= 4.0

        known.append((score, value))

    if not known:
        return None

    known.sort(key=lambda x: x[0], reverse=True)
    return known[0][1]