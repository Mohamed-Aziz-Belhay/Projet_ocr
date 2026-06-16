"""
app/services/result_formatter.py
Formatting helpers for downstream integrations.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from app.schemas.ocr import ExtractionResponse, FieldResult
from app.schemas.template import TemplateSpec


def format_as_flat_dict(fields: List[FieldResult], template: Optional[TemplateSpec] = None) -> Dict[str, Any]:
    """Return a downstream-ready flat dictionary.

    Supports both raw template field names (e.g. ``numero_facture``) and API
    aliases/output keys (e.g. ``invoice_number``), then applies
    ``template.output_mapping`` when present.
    """
    mapping = template.output_mapping if template else {}
    raw_to_output = {}
    if template:
        raw_to_output = {spec.name: (spec.output_key or spec.name) for spec in template.fields}
    result: Dict[str, Any] = {}
    for field in fields:
        intermediate_key = raw_to_output.get(field.name, field.name)
        final_key = mapping.get(intermediate_key, intermediate_key)
        result[final_key] = field.value
    return result


def format_response(
    response: ExtractionResponse,
    template: Optional[TemplateSpec] = None,
    include_raw: bool = False,
    include_confidence: bool = True,
    include_diagnostics: bool = False,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "job_id": response.job_id,
        "status": response.status,
        "template_id": response.template_id,
        "engine": response.engine_used,
        "language": response.language_detected,
        "processing_ms": response.processing_time_ms,
        "data": format_as_flat_dict(response.fields, template),
        "normalized_data": response.normalized_data,
    }
    if include_confidence:
        out["confidence"] = {
            "global": response.global_confidence,
            "fields": {f.name: f.confidence for f in response.fields},
        }
    if include_raw and response.raw_text:
        out["raw_text"] = response.raw_text
    if include_diagnostics and response.diagnostics:
        out["diagnostics"] = response.diagnostics
    if response.warnings:
        out["warnings"] = response.warnings
    return out