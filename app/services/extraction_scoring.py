"""
app/services/extraction_scoring.py
Scoring helpers for API-level status and review decisions.
"""
from __future__ import annotations
from typing import List, Optional

from app.core.settings import get_settings
from app.schemas.ocr import FieldResult
from app.schemas.template import TemplateSpec

settings = get_settings()


def compute_global_confidence(fields: List[FieldResult], template: Optional[TemplateSpec] = None) -> float:
    if not fields:
        return 0.0
    total_weight = 0.0
    weighted_sum = 0.0
    for field in fields:
        weight = 1.0
        if template:
            for spec in template.fields:
                key = spec.output_key or spec.name
                if key == field.name:
                    weight = spec.confidence_weight
                    break
        weighted_sum += field.confidence * weight
        total_weight += weight
    return round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.0


def compute_business_quality(fields: List[FieldResult]) -> float:
    if not fields:
        return 0.0
    vals = [f.confidence for f in fields if f.value is not None]
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def compute_review_required(fields: List[FieldResult], warnings: List[str]) -> bool:
    if any(getattr(f, "review_required", False) for f in fields):
        return True
    if any("ambigu" in w.lower() or "review" in w.lower() for w in warnings):
        return True
    critical = {"id_number", "last_name", "first_name"}
    for field in fields:
        if field.name in critical and (field.value is None or field.confidence < settings.REVIEW_REQUIRED_THRESHOLD):
            return True
    return False


def compute_extraction_status(fields: List[FieldResult], warnings: List[str]) -> str:
    if not fields:
        return "failed"
    if compute_review_required(fields, warnings):
        return "review_required"
    required_missing = sum(1 for w in warnings if "required field" in w.lower() or "champ requis" in w.lower())
    validation_errors = sum(1 for f in fields if not f.validated and f.value is not None)
    if required_missing > 0 or validation_errors > 0:
        return "partial"
    return "success"


def compute_field_coverage(fields: List[FieldResult], template: Optional[TemplateSpec] = None) -> dict:
    total = len(template.fields) if template else len(fields)
    extracted = sum(1 for f in fields if f.value is not None)
    validated = sum(1 for f in fields if f.validated)
    review_required = sum(1 for f in fields if f.review_required)
    return {
        "total_fields": total,
        "extracted": extracted,
        "validated": validated,
        "review_required": review_required,
        "coverage_pct": round(extracted / total * 100, 1) if total > 0 else 0.0,
        "validation_rate_pct": round(validated / extracted * 100, 1) if extracted > 0 else 0.0,
    }