"""
app/pipeline/runner.py

Unified pipeline router.
Adds file_path to request.metadata so Swin can classify the uploaded image.

FIX PDF:
- uses app.pipeline.io.load_file_as_pages()
- supports PDF/images through a single loader
- uses first page for the current OCR pipeline
"""
from __future__ import annotations

import time
from typing import Any, Optional

from app.core.logging import get_logger
from app.pipeline.cin_runner import CINPipelineRunner
from app.pipeline.common import PipelinePreprocessor
from app.pipeline.generic_runner import GenericPipelineRunner
from app.pipeline.io import load_file_as_pages
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.utils.logging import log_call

log = get_logger(__name__)


class PipelineRunner:
    def __init__(self):
        self.preprocessor = PipelinePreprocessor()
        self.cin_runner = CINPipelineRunner()
        self.generic_runner = GenericPipelineRunner()

    def _is_cin_request(
        self,
        request: ExtractionRequest,
        template: Optional[Any] = None,
    ) -> bool:
        template_id = request.template_id or getattr(template, "id", None)
        document_type = getattr(request, "document_type", "auto")
        return template_id == "cin_tn" or document_type == "cin_tn"

    def _load_first_page(self, file_path: str, request: ExtractionRequest):
        """
        Load image or PDF as BGR numpy image.

        Current pipeline is mono-page:
        - image files return one page
        - PDF files may return multiple pages, but we use page 1 for now

        Later improvement:
        - loop over all pages
        - merge results per page
        """
        metadata = dict(getattr(request, "metadata", {}) or {})

        # 220 DPI = good balance for OCR.
        # Full/debug mode can later use 300 DPI if needed.
        dpi = 220

        engine = str(getattr(request, "engine", "") or "").lower()
        cin_mode = str(getattr(request, "cin_mode", "") or "").lower()

        if engine in {"full", "debug"} or cin_mode in {"full", "debug"}:
            dpi = 300

        pages = load_file_as_pages(file_path, dpi=dpi)

        if not pages:
            raise RuntimeError(f"No page could be loaded from file: {file_path}")

        metadata["page_count"] = len(pages)
        metadata["loaded_page_index"] = 0
        metadata["pdf_dpi"] = dpi

        request = request.model_copy(update={"metadata": metadata})

        if len(pages) > 1:
            log.warning(
                "Multi-page document loaded; only first page is processed for now",
                extra={
                    "file_path": file_path,
                    "page_count": len(pages),
                    "dpi": dpi,
                },
            )

        return pages[0], request

    @log_call(__name__)
    def run(
        self,
        file_path: str,
        request: ExtractionRequest,
        template: Optional[Any] = None,
        job_id: Optional[str] = None,
    ) -> ExtractionResponse:
        started = time.perf_counter()
        job_id = job_id or "local-job"

        image, request = self._load_first_page(file_path, request)

        metadata = dict(getattr(request, "metadata", {}) or {})
        metadata["file_path"] = file_path
        request = request.model_copy(update={"metadata": metadata})

        if self._is_cin_request(request, template):
            prep = self.preprocessor.prepare_for_cin(image)
            result = self.cin_runner.run(prep=prep, request=request, job_id=job_id)
        else:
            prep = self.preprocessor.prepare(image)
            result = self.generic_runner.run(
                prep=prep,
                request=request,
                template=template,
                job_id=job_id,
            )

        if getattr(result, "processing_time_ms", 0) <= 0:
            result.processing_time_ms = int((time.perf_counter() - started) * 1000)

        return result


_runner: Optional[PipelineRunner] = None


def get_pipeline_runner() -> PipelineRunner:
    global _runner
    if _runner is None:
        _runner = PipelineRunner()
    return _runner


def run_pipeline(
    file_path: str,
    request: Optional[ExtractionRequest] = None,
    template: Optional[Any] = None,
    engine_name: str = "auto",
    fast_mode: bool = False,
    language_hint: Optional[str] = None,
    job_id: Optional[str] = None,
    include_diagnostics: bool = True,
) -> ExtractionResponse:
    if request is None:
        template_id = getattr(template, "id", None) if template is not None else None
        request = ExtractionRequest(
            template_id=template_id,
            engine=engine_name,
            fast_mode=fast_mode,
            language_hint=language_hint,
            include_diagnostics=include_diagnostics,
        )
    elif template is not None and not request.template_id:
        template_id = getattr(template, "id", None)
        if template_id:
            request = request.model_copy(update={"template_id": template_id})

    return get_pipeline_runner().run(
        file_path=file_path,
        request=request,
        template=template,
        job_id=job_id,
    )
"""
app/pipeline/runner.py

Unified pipeline router.
Adds file_path to request.metadata so Swin can classify the uploaded image.
"""
""""
from __future__ import annotations

import time
from typing import Any, Optional

from app.core.logging import get_logger
from app.pipeline.cin_runner import CINPipelineRunner
from app.pipeline.common import PipelinePreprocessor, load_image
from app.pipeline.generic_runner import GenericPipelineRunner
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.utils.logging import log_call

log = get_logger(__name__)


class PipelineRunner:
    def __init__(self):
        self.preprocessor = PipelinePreprocessor()
        self.cin_runner = CINPipelineRunner()
        self.generic_runner = GenericPipelineRunner()

    def _is_cin_request(
        self,
        request: ExtractionRequest,
        template: Optional[Any] = None,
    ) -> bool:
        template_id = request.template_id or getattr(template, "id", None)
        document_type = getattr(request, "document_type", "auto")
        return template_id == "cin_tn" or document_type == "cin_tn"

    @log_call(__name__)
    def run(
        self,
        file_path: str,
        request: ExtractionRequest,
        template: Optional[Any] = None,
        job_id: Optional[str] = None,
    ) -> ExtractionResponse:
        started = time.perf_counter()
        job_id = job_id or "local-job"

        image = load_image(file_path)

        metadata = dict(getattr(request, "metadata", {}) or {})
        metadata["file_path"] = file_path
        request = request.model_copy(update={"metadata": metadata})

        if self._is_cin_request(request, template):
            prep = self.preprocessor.prepare_for_cin(image)
            result = self.cin_runner.run(prep=prep, request=request, job_id=job_id)
        else:
            prep = self.preprocessor.prepare(image)
            result = self.generic_runner.run(
                prep=prep,
                request=request,
                template=template,
                job_id=job_id,
            )

        if getattr(result, "processing_time_ms", 0) <= 0:
            result.processing_time_ms = int((time.perf_counter() - started) * 1000)

        return result


_runner: Optional[PipelineRunner] = None


def get_pipeline_runner() -> PipelineRunner:
    global _runner
    if _runner is None:
        _runner = PipelineRunner()
    return _runner


def run_pipeline(
    file_path: str,
    request: Optional[ExtractionRequest] = None,
    template: Optional[Any] = None,
    engine_name: str = "auto",
    fast_mode: bool = False,
    language_hint: Optional[str] = None,
    job_id: Optional[str] = None,
    include_diagnostics: bool = True,
) -> ExtractionResponse:
    if request is None:
        template_id = getattr(template, "id", None) if template is not None else None
        request = ExtractionRequest(
            template_id=template_id,
            engine=engine_name,
            language_hint=language_hint,
            include_diagnostics=include_diagnostics,
        )
    elif template is not None and not request.template_id:
        template_id = getattr(template, "id", None)
        if template_id:
            request = request.model_copy(update={"template_id": template_id})

    return get_pipeline_runner().run(
        file_path=file_path,
        request=request,
        template=template,
        job_id=job_id,
    )
"""