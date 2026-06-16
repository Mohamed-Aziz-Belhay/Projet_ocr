"""
app/core/errors.py
Custom exceptions + FastAPI exception handlers.
"""
from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class OCRServiceError(Exception):
    """Base for all domain errors."""
    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, detail: str, **context):
        self.detail = detail
        self.context = context
        super().__init__(detail)


class TemplateNotFoundError(OCRServiceError):
    status_code = 404
    error_code = "TEMPLATE_NOT_FOUND"


class TemplateValidationError(OCRServiceError):
    status_code = 422
    error_code = "TEMPLATE_INVALID"


class UnsupportedFileTypeError(OCRServiceError):
    status_code = 415
    error_code = "UNSUPPORTED_FILE_TYPE"


class FileTooLargeError(OCRServiceError):
    status_code = 413
    error_code = "FILE_TOO_LARGE"


class EngineUnavailableError(OCRServiceError):
    status_code = 503
    error_code = "ENGINE_UNAVAILABLE"


class JobNotFoundError(OCRServiceError):
    status_code = 404
    error_code = "JOB_NOT_FOUND"


class ExtractionError(OCRServiceError):
    status_code = 422
    error_code = "EXTRACTION_FAILED"


def _make_error_body(exc: OCRServiceError, request: Request) -> dict:
    return {
        "error": exc.error_code,
        "detail": exc.detail,
        "path": str(request.url.path),
        **({"context": exc.context} if exc.context else {}),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(OCRServiceError)
    async def ocr_error_handler(request: Request, exc: OCRServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_make_error_body(exc, request),
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_ERROR",
                "detail": str(exc),
                "path": str(request.url.path),
            },
        )