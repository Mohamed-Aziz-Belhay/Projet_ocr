"""
app/api/deps.py
Centralised FastAPI dependency injection.
All services are resolved here — routers import only from this module.
"""
from __future__ import annotations
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.security import verify_api_key
from app.core.settings import get_settings, Settings
from app.services.ocr_service import get_ocr_service, OCRService
from app.services.template_service import get_template_service, TemplateService
from app.services.job_service import get_job_service, JobService
from app.services.storage_service import get_storage_service, StorageService
from app.services.document_orchestrator import get_orchestrator, DocumentOrchestrator
from app.services.generic_extraction_service import get_generic_extraction_service, GenericExtractionService
from app.services.benchmark_service import get_benchmark_service, BenchmarkService

# ── Typed dependency aliases ──────────────────────────────────────────────────
# Use these in router function signatures for clean, explicit DI

SettingsDep         = Annotated[Settings,                Depends(get_settings)]
AuthDep             = Annotated[str,                     Depends(verify_api_key)]
OCRServiceDep       = Annotated[OCRService,              Depends(get_ocr_service)]
TemplateServiceDep  = Annotated[TemplateService,         Depends(get_template_service)]
JobServiceDep       = Annotated[JobService,              Depends(get_job_service)]
StorageServiceDep   = Annotated[StorageService,          Depends(get_storage_service)]
OrchestratorDep     = Annotated[DocumentOrchestrator,    Depends(get_orchestrator)]
GenericExtractDep   = Annotated[GenericExtractionService,Depends(get_generic_extraction_service)]
BenchmarkDep        = Annotated[BenchmarkService,        Depends(get_benchmark_service)]

# ── Optional: request-scoped context ─────────────────────────────────────────

class RequestContext:
    """Lightweight per-request container for cross-cutting concerns."""
    def __init__(
        self,
        api_key: str,
        settings: Settings,
    ):
        self.api_key  = api_key
        self.settings = settings

    @property
    def is_dev(self) -> bool:
        return self.settings.ENVIRONMENT == "development"


RequestContextDep = Annotated[RequestContext, Depends(RequestContext)]