"""
app/schemas/ocr.py

PFE-oriented OCR schemas.
- document_type: auto/cin_tn/invoice/passport/registre_commerce/custom/id_document
- processing_mode: generic OCR speed/accuracy control
- cin_mode: CIN-specialized speed/accuracy control
- fast_mode remains hidden only for backward compatibility
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


CinMode = Literal["fast", "balanced", "full"]
ProcessingMode = Literal["fast", "balanced", "full"]
DocumentType = Literal[
    "auto",
    "cin_tn",
    "invoice",
    "passport",
    "registre_commerce",
    "custom",
    "id_document",
]


class ExtractionRequest(BaseModel):
    template_id: Optional[str] = Field(
        default=None,
        description=(
            "Template slug to use. Examples: cin_tn, invoice_generic, "
            "passport_generic, registre_commerce, midv_svk_id. "
            "If omitted, auto-detection is attempted."
        ),
    )

    document_type: DocumentType = Field(
        default="auto",
        description=(
            "Document family selected by user or auto. Examples: auto, "
            "cin_tn, invoice, passport, registre_commerce, id_document, custom."
        ),
    )

    engine: Optional[str] = Field(
        default="auto",
        description="Requested OCR engine or strategy: auto, paddle, tesseract, easyocr, surya.",
    )

    processing_mode: ProcessingMode = Field(
        default="balanced",
        description=(
            "Generic pipeline mode. fast=ROI/template-first quick extraction; "
            "balanced=ROI + text fallback; full=more exhaustive fallback."
        ),
    )

    cin_mode: CinMode = Field(
        default="balanced",
        description=(
            "CIN pipeline mode. fast=easyocr_boxes then paddle_boxes only if critical fields are missing; "
            "balanced=targeted fallback; full=exhaustive."
        ),
    )

    language_hint: Optional[str] = Field(
        default=None,
        description="BCP-47 code, e.g. 'fr', 'ar', 'en'.",
    )

    webhook_url: Optional[HttpUrl] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    include_diagnostics: bool = Field(
        default=True,
        description="Include detailed diagnostics in API response.",
    )

    # Hidden backward compatibility field. Do not expose in FastAPI form.
    fast_mode: bool = Field(default=False, exclude=True)


class FieldResult(BaseModel):
    name: str
    value: Optional[Any]
    confidence: float = Field(ge=0.0, le=1.0)
    validated: bool
    raw_text: Optional[str] = None
    raw_template_field: Optional[str] = None
    error: Optional[str] = None
    selected_engine: Optional[str] = None
    selected_source: Optional[str] = None
    review_required: bool = False
    reasons: List[str] = Field(default_factory=list)


class ExtractionResponse(BaseModel):
    job_id: str
    status: Literal["success", "partial", "failed", "review_required"]
    template_id: Optional[str]
    document_type: Optional[str] = None
    document_variant: Optional[str] = None
    engine_used: str
    language_detected: Optional[str]
    global_confidence: float = Field(ge=0.0, le=1.0)
    quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fields: List[FieldResult]
    normalized_data: Dict[str, Any] = Field(default_factory=dict)
    routing: Optional[Dict[str, Any]] = None
    business_validation: Optional[Dict[str, Any]] = None
    diagnostics: Optional[Dict[str, Any]] = None
    raw_text: Optional[str] = None
    processing_time_ms: int
    warnings: List[str] = Field(default_factory=list)
    review_reasons: List[str] = Field(default_factory=list)
