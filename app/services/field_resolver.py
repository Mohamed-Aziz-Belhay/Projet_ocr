"""
app/services/field_resolver.py
Business-aware candidate scoring and resolution.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional

from app.utils.date_validation import parse_and_normalize
from app.utils.rtl_text import (
    cleanup_arabic_text,
    contains_arabic,
    has_forbidden_label_token,
    is_probable_name_value,
    is_probable_place_value,
    normalize_arabic_digits,
    plausible_arabic_phrase,
    score_arabic_phrase_quality,
)

FORBIDDEN_LABELS = {
    "الجنسية", "الجمهورية", "التونسية", "الجنسيه",
    "اللقب", "الاسم", "تاريخ", "مولد",
    "الولادة", "محل", "الميلاد", "الجنس", "وطنية",
    "الوطنية", "بطاقة", "التعريف", "وطنيه",
}

AR_MONTHS = {
    "جانفي", "جانفي", "فيفري", "فبراير",
    "مارس", "أفريل", "افريل", "ماي",
    "جوان", "جويلية", "جويلي", "أوت", "اوت",
    "سبتمبر", "أكتوبر", "اكتوبر", "نوفمبر", "ديسمبر",
}


@dataclass
class FieldCandidate:
    field_name: str
    engine: str
    source: str
    value: Optional[str]
    raw_text: str
    ocr_confidence: float
    business_score: float = 0.0
    valid: bool = False
    reasons: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedField:
    field_name: str
    value: Optional[str]
    final_score: float
    selected_engine: Optional[str]
    selected_source: Optional[str]
    validated: bool
    candidates: List[FieldCandidate]
    review_required: bool
    reasons: List[str] = field(default_factory=list)
    raw_text: Optional[str] = None


def _normalize_digits(text: str) -> str:
    return re.sub(r"\D", "", normalize_arabic_digits(text or ""))


def _clean_name_candidate(text: str) -> Optional[str]:
    return plausible_arabic_phrase(text, max_tokens=2, forbidden=FORBIDDEN_LABELS)


def _source_bonus(source: str, preferred_zone: Optional[str] = None) -> float:
    bonus = 0.0
    if preferred_zone and source == f"zone:{preferred_zone}":
        bonus += 0.10
    if source.startswith("zone:") and not source.endswith("right_text"):
        bonus += 0.04
    if source.endswith("right_text"):
        bonus -= 0.04
    return bonus


def _score_id(candidate: FieldCandidate, rules: Dict[str, Any]) -> FieldCandidate:
    digits = _normalize_digits(candidate.value or candidate.raw_text)
    expected_len = int(rules.get("expected_length", 8))
    if len(digits) == expected_len:
        candidate.value = digits
        candidate.valid = True
        candidate.business_score = round(
            min(0.99, 0.76 + candidate.ocr_confidence * 0.20 + _source_bonus(candidate.source, rules.get("prefer_zone"))), 3
        )
        candidate.reasons.append("8 digits exact")
        return candidate
    if candidate.source == "zone:id_number" and 8 <= len(digits) <= 9:
        candidate.value = digits[:8]
        candidate.valid = True
        candidate.business_score = 0.74
        candidate.reasons.append("reconstructed from digit groups")
        return candidate
    candidate.valid = False
    candidate.business_score = 0.0
    candidate.reasons.append("no 8-digit candidate")
    return candidate


def _hard_reject_name(cleaned: str, raw: str, rules: Dict[str, Any]) -> Optional[str]:
    forbidden = set(FORBIDDEN_LABELS)
    forbidden.update(rules.get("forbidden_tokens") or [])
    if not cleaned or not contains_arabic(cleaned):
        return "no arabic name candidate"
    if has_forbidden_label_token(raw, forbidden):
        return "label pollution"
    if not is_probable_name_value(cleaned, forbidden):
        return "name shape invalid"
    toks = cleaned.split()
    if len(toks) == 1 and len(toks[0]) < 3:
        return "name too short"
    return None


def _score_name(candidate: FieldCandidate, field_name: str, rules: Dict[str, Any]) -> FieldCandidate:
    cleaned = _clean_name_candidate(candidate.value or candidate.raw_text)
    reject  = _hard_reject_name(cleaned or "", candidate.raw_text, rules)
    if reject:
        candidate.valid = False
        candidate.business_score = 0.0
        candidate.reasons.append(reject)
        return candidate
    candidate.value = cleaned
    candidate.valid = True
    score  = 0.28 + candidate.ocr_confidence * 0.38 + score_arabic_phrase_quality(candidate.raw_text) * 0.22
    score += _source_bonus(candidate.source, rules.get("prefer_zone"))
    if candidate.meta.get("anchor_hit"):
        score += 0.12
        candidate.reasons.append("anchor hit")
    if candidate.meta.get("same_line_anchor"):
        score += 0.05
        candidate.reasons.append("same line anchor")
    if candidate.source.endswith("right_text"):
        score -= 0.05
    if len((candidate.value or "").split()) > 2:
        score -= 0.15
    candidate.business_score = round(max(0.0, min(0.97, score)), 3)
    return candidate


def _score_birth_date(candidate: FieldCandidate, rules: Dict[str, Any]) -> FieldCandidate:
    text       = cleanup_arabic_text(candidate.value or candidate.raw_text)
    normalized = parse_and_normalize(text)
    if normalized and re.match(r"\d{4}-\d{2}-\d{2}$", normalized):
        candidate.value         = normalized
        candidate.valid         = True
        candidate.business_score = round(
            min(0.98, 0.54 + candidate.ocr_confidence * 0.34 + _source_bonus(candidate.source, rules.get("prefer_zone"))), 3
        )
        candidate.reasons.append("valid date")
        return candidate
    candidate.valid          = False
    candidate.business_score = 0.0
    candidate.reasons.append("date not found")
    return candidate


def _score_birth_place(candidate: FieldCandidate, rules: Dict[str, Any]) -> FieldCandidate:
    cleaned = plausible_arabic_phrase(
        candidate.value or candidate.raw_text, max_tokens=2, forbidden=FORBIDDEN_LABELS
    )
    if not cleaned or not is_probable_place_value(cleaned, rules.get("forbidden_tokens")):
        candidate.valid          = False
        candidate.business_score = 0.0
        candidate.reasons.append("no place candidate")
        return candidate
    if has_forbidden_label_token(candidate.raw_text, rules.get("forbidden_tokens")):
        candidate.valid          = False
        candidate.business_score = 0.0
        candidate.reasons.append("label pollution")
        return candidate
    candidate.value = cleaned
    candidate.valid = True
    score  = 0.18 + candidate.ocr_confidence * 0.35 + score_arabic_phrase_quality(candidate.raw_text) * 0.20
    score += _source_bonus(candidate.source, rules.get("prefer_zone"))
    if candidate.meta.get("anchor_hit"):
        score += 0.08
        candidate.reasons.append("anchor hit")
    candidate.business_score = round(max(0.0, min(0.90, score)), 3)
    return candidate


def score_candidate(candidate: FieldCandidate, rules: Optional[Dict[str, Any]] = None) -> FieldCandidate:
    rules = rules or {}
    if candidate.field_name == "id_number":
        return _score_id(candidate, rules)
    if candidate.field_name in {"last_name", "first_name"}:
        return _score_name(candidate, candidate.field_name, rules)
    if candidate.field_name == "birth_date":
        return _score_birth_date(candidate, rules)
    if candidate.field_name == "birth_place":
        return _score_birth_place(candidate, rules)
    return candidate


def resolve_field(
    field_name: str,
    candidates: List[FieldCandidate],
    rules: Optional[Dict[str, Any]] = None,
) -> ResolvedField:
    rules            = rules or {}
    required         = bool(rules.get("required", False))
    review_threshold = float(rules.get("review_threshold", 0.78))

    scored = [score_candidate(c, rules) for c in candidates]
    scored = [c for c in scored if c.valid and c.value is not None]
    scored.sort(key=lambda c: c.business_score, reverse=True)
    best = scored[0] if scored else None

    if not best:
        reasons = ["field unresolved"]
        if required:
            reasons.append("required field missing")
        return ResolvedField(
            field_name=field_name, value=None, final_score=0.0,
            selected_engine=None, selected_source=None,
            validated=False, candidates=scored, review_required=required,
            reasons=reasons, raw_text=None,
        )

    review = best.business_score < review_threshold
    if len(scored) >= 2 and abs(scored[0].business_score - scored[1].business_score) < 0.02:
        review = True
    reasons = list(best.reasons)
    if review:
        reasons.append("review threshold not met")

    return ResolvedField(
        field_name=field_name,
        value=best.value,
        final_score=best.business_score,
        selected_engine=best.engine,
        selected_source=best.source,
        validated=not review,
        candidates=scored,
        review_required=review,
        reasons=reasons,
        raw_text=best.raw_text,
    )


def resolve_fields(
    field_candidates: Dict[str, List[FieldCandidate]],
    field_rules: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, ResolvedField]:
    field_rules = field_rules or {}
    return {
        field_name: resolve_field(field_name, candidates, field_rules.get(field_name) or {})
        for field_name, candidates in field_candidates.items()
    }