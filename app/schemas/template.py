"""
app/schemas/template.py
Pydantic models that mirror the YAML template structure.

This version extends templates with pipeline policies, engines,
field-level review thresholds and fixed zones.
"""
from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class AnchorSpec(BaseModel):
    text: str
    required: bool = False


class ValidationSpec(BaseModel):
    type: Literal["regex", "date", "number", "enum", "any"] = "any"
    pattern: Optional[str] = None
    date_formats: List[str] = Field(default_factory=list)
    allowed_values: List[Any] = Field(default_factory=list)
    min_length: Optional[int] = None
    max_length: Optional[int] = None


class NormalizationSpec(BaseModel):
    strip: bool = True
    uppercase: bool = False
    lowercase: bool = False
    remove_spaces: bool = False
    custom_replace: Dict[str, str] = Field(default_factory=dict)


class FieldSpec(BaseModel):
    name: str
    label: Optional[str] = None
    extraction_method: Literal["regex", "anchor", "position", "llm_hint"] = "regex"
    patterns: List[str] = Field(default_factory=list)
    anchors: List[AnchorSpec] = Field(default_factory=list)
    validation: ValidationSpec = Field(default_factory=ValidationSpec)
    normalization: NormalizationSpec = Field(default_factory=NormalizationSpec)
    required: bool = False
    output_key: Optional[str] = None
    confidence_weight: float = 1.0


class TemplateSpec(BaseModel):
    id: str
    name: str
    version: str = "1.0"
    description: Optional[str] = None
    doc_family: Optional[str] = None
    language_hints: List[str] = Field(default_factory=list)
    preferred_engine: str = "auto"
    anchors_required: List[str] = Field(default_factory=list)
    fields: List[FieldSpec] = Field(default_factory=list)
    postprocess_hooks: List[str] = Field(default_factory=list)
    output_mapping: Dict[str, str] = Field(default_factory=dict)

    # Architecture-oriented additions
    pipeline: str = "generic_template_v1"
    fixed_zones: Dict[str, List[float]] = Field(default_factory=dict)
    engines: Dict[str, Any] = Field(default_factory=dict)
    field_policies: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    review_policy: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class TemplateCreateRequest(BaseModel):
    template: TemplateSpec


class TemplateUpdateRequest(BaseModel):
    template: TemplateSpec


class TemplateListItem(BaseModel):
    id: str
    name: str
    version: str
    doc_family: Optional[str]
    field_count: int
    pipeline: Optional[str] = None
