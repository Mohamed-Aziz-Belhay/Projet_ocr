"""
app/services/ocr_service.py

API-facing OCR service.
Must preserve the full ExtractionRequest. If this service decomposes the request
into engine_name/fast_mode/language_hint only, request.processing_mode and
request.cin_mode are lost.
"""
from __future__ import annotations

from typing import Optional

from app.core.logging import get_logger
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.services.document_orchestrator import DocumentOrchestrator
from app.services.storage_service import get_storage_service

log = get_logger(__name__)


class OCRService:
    def __init__(self):
        self.orchestrator = DocumentOrchestrator()
        self.storage = get_storage_service()

    def extract_sync(
        self,
        file_path: str,
        request: ExtractionRequest,
        job_id: Optional[str] = None,
    ) -> ExtractionResponse:
        return self.orchestrator.process(
            file_path=file_path,
            request=request,
            job_id=job_id,
        )

    async def extract_async(
        self,
        file_path: str,
        request: ExtractionRequest,
        job_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> str:
        import asyncio
        import uuid

        jid = job_id or str(uuid.uuid4())

        if org_id:
            from app.routers.routes_extract import _run_extraction_fallback

            asyncio.create_task(
                _run_extraction_fallback(
                    job_id=jid,
                    org_id=org_id,
                    file_path=file_path,
                    request=request,
                )
            )
            return jid

        asyncio.create_task(
            self._run_simple(
                job_id=jid,
                file_path=file_path,
                request=request,
            )
        )
        return jid

    async def _run_simple(
        self,
        job_id: str,
        file_path: str,
        request: ExtractionRequest,
    ) -> None:
        try:
            result = self.extract_sync(
                file_path=file_path,
                request=request,
                job_id=job_id,
            )
            result_dict = result.model_dump(mode="json")
            self.storage.save_result(job_id, result_dict)

            if request.webhook_url:
                from app.services.webhook_service import send_webhook
                await send_webhook(str(request.webhook_url), result_dict)

        except Exception as exc:
            log.error("Simple async failed", extra={"job_id": job_id, "error": str(exc)})

        finally:
            try:
                self.storage.delete_upload(file_path)
            except Exception:
                pass


_ocr_service: Optional[OCRService] = None


def get_ocr_service() -> OCRService:
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service
