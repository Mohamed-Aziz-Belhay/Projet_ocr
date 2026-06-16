"""
app/services/document_orchestrator.py

Preserves the full ExtractionRequest and forwards it to the unified pipeline.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from app.core.logging import get_logger
from app.pipeline.runner import run_pipeline
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.services.template_service import get_template_service

log = get_logger(__name__)


class DocumentOrchestrator:
    def __init__(self):
        self.templates = get_template_service()

    def _safe_get_template(self, template_id: Optional[str]) -> Optional[Any]:
        if not template_id:
            return None
        try:
            return self.templates.get(template_id)
        except Exception as exc:
            log.warning(
                "Template lookup failed",
                extra={"template_id": template_id, "error": str(exc)},
            )
            return None

    def process(
        self,
        file_path: str,
        request: ExtractionRequest,
        job_id: Optional[str] = None,
    ) -> ExtractionResponse:
        job_id = job_id or str(uuid.uuid4())
        template = self._safe_get_template(request.template_id)
        return run_pipeline(
            file_path=file_path,
            request=request,
            template=template,
            job_id=job_id,
        )


_orchestrator: Optional[DocumentOrchestrator] = None


def get_orchestrator() -> DocumentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = DocumentOrchestrator()
    return _orchestrator
