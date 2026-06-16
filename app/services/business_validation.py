from __future__ import annotations

from typing import Any, Dict, List

from app.core.settings import get_settings

settings = get_settings()

_CRITICAL = {"id_number", "last_name", "first_name"}
_OPTIONAL = {"birth_date", "birth_place"}
_ALL_EXPECTED = _CRITICAL | _OPTIONAL


def _is_present(value: Any) -> bool:
    return value not in (None, "", [])


def assess_cin_document(
    field_dicts: List[Dict[str, Any]],
    *,
    quality_score: float = 0.0,
    success_threshold: float | None = None,
) -> Dict[str, Any]:
    success_threshold = float(
        success_threshold
        if success_threshold is not None
        else getattr(settings, "CIN_SUCCESS_MIN_BUSINESS_CONFIDENCE", 0.88)
    )

    by_name = {f.get("name"): f for f in (field_dicts or [])}
    warnings: List[str] = []
    review_reasons: List[str] = []

    critical_ok = 0
    valid_count = 0
    present_count = 0

    confidence_sum = 0.0
    confidence_count = 0

    for name in _ALL_EXPECTED:
        field = by_name.get(name, {}) or {}
        value = field.get("value")
        validated = bool(field.get("validated"))
        conf = float(field.get("confidence") or 0.0)

        if _is_present(value):
            present_count += 1

        if validated and _is_present(value):
            valid_count += 1
            confidence_sum += conf
            confidence_count += 1

        if name in _CRITICAL:
            if validated and _is_present(value):
                critical_ok += 1
            else:
                review_reasons.append(f"critical field missing or invalid: {name}")
                warnings.append(f"Champ critique à vérifier: {name}")

    avg_conf = confidence_sum / confidence_count if confidence_count else 0.0
    critical_ratio = critical_ok / len(_CRITICAL) if _CRITICAL else 0.0
    field_coverage = valid_count / len(_ALL_EXPECTED) if _ALL_EXPECTED else 0.0
    quality_score = float(quality_score or 0.0)

    # New scoring:
    # - prioritize validated extracted fields
    # - use quality only as a small factor, not as a blocker
    business_confidence = round(
        min(
            1.0,
            0.60 * avg_conf
            + 0.25 * critical_ratio
            + 0.10 * field_coverage
            + 0.05 * quality_score,
        ),
        4,
    )

    birth_date_present = _is_present(by_name.get("birth_date", {}).get("value"))
    birth_place_present = _is_present(by_name.get("birth_place", {}).get("value"))

    if not birth_date_present and getattr(settings, "CIN_REVIEW_IF_DATE_MISSING", True):
        review_reasons.append("birth_date missing")
        warnings.append("Date de naissance à vérifier")

    if not birth_place_present and getattr(settings, "CIN_REVIEW_IF_BIRTH_PLACE_MISSING", False):
        review_reasons.append("birth_place missing")
        warnings.append("Lieu de naissance à vérifier")

    required_critical = int(getattr(settings, "CIN_SUCCESS_MIN_CRITICAL_FIELDS", 3))

    # Success rules:
    # 1) All critical fields valid + at least 4 valid fields overall + threshold reached
    # 2) Or all 5 expected fields valid => success directly
    all_expected_valid = valid_count >= len(_ALL_EXPECTED)
    enough_for_success = (
        critical_ok >= required_critical
        and valid_count >= 4
        and business_confidence >= success_threshold
    )

    review_required = False
    status = "failed"

    if all_expected_valid:
        status = "success"
    elif enough_for_success:
        status = "success"
    elif present_count > 0:
        status = "review_required"
        review_required = True
    else:
        status = "failed"

    if review_reasons:
        review_required = True
        if status == "success":
            # keep success only if all expected fields are validated
            if not all_expected_valid:
                status = "review_required"

    return {
        "status": status,
        "review_required": review_required,
        "review_reasons": list(dict.fromkeys(review_reasons)),
        "warnings": list(dict.fromkeys(warnings)),
        "business_confidence": business_confidence,
        "critical_fields_ok": critical_ok,
        "valid_fields_ok": valid_count,
        "present_fields": present_count,
        "quality_score": quality_score,
        "avg_field_confidence": round(avg_conf, 4),
        "field_coverage": round(field_coverage, 4),
    }