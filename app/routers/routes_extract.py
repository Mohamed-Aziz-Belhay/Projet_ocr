"""
app/routers/routes_extract.py

OCR extraction routes.

Version optimisée:
- Validation claire de l'upload.
- Vérification que le fichier sauvegardé existe et n'est pas vide.
- Document type guard avant OCR complet.
- Guard plus rapide: DPI réduit + langues limitées selon le type sélectionné.
- Pas de guard pour auto/custom.
- Suppression des prints debug et de l'import Flask incorrect.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tenant import TenantDep
from app.core.settings import get_settings
from app.core.security import decode_access_token
from app.core.rbac import require_extract_permission
from app.db.models.user import User
from app.db.models.extraction_history import ExtractionHistory
from app.db.session import get_db
from app.engines.circuit_breaker import get_circuit_breaker
from app.schemas.ocr import ExtractionRequest, ExtractionResponse
from app.schemas.responses import SuccessResponse
from app.services.audit_service import get_audit_service
from app.services.ocr_service import get_ocr_service
from app.services.storage_service import StorageService, get_storage_service

from app.services.document_type_guard import (
    detect_document_type_from_text,
    is_type_compatible,
    normalize_document_type,
)

log = get_logger(__name__)
router = APIRouter(prefix="/extract", tags=["Extraction"])


ProcessingMode = Literal["fast", "balanced", "full", "debug", "diagnostic"]
CinMode = Literal["fast", "balanced", "full"]


# Cache simple des instances PaddleOCR utilisées par le guard.
# Gain de temps important en local: évite de réinitialiser le modèle à chaque requête.
_GUARD_OCR_CACHE: dict[str, object] = {}


def _parse_request(
    template_id: Optional[str] = Form(None),
    document_type: Literal[
        "auto",
        "cin_tn",
        "invoice",
        "passport",
        "registre_commerce",
        "custom",
        "id_document",
    ] = Form("auto"),
    engine: Optional[str] = Form("auto"),
    processing_mode: ProcessingMode = Form("balanced"),
    cin_mode: CinMode = Form("balanced"),
    language_hint: Optional[str] = Form(None),
    webhook_url: Optional[str] = Form(None),
    include_diagnostics: bool = Form(True),
) -> ExtractionRequest:
    return ExtractionRequest(
        template_id=template_id or None,
        document_type=document_type,
        engine=engine or "auto",
        processing_mode=processing_mode,
        cin_mode=cin_mode,
        language_hint=language_hint or None,
        webhook_url=webhook_url or None,
        include_diagnostics=include_diagnostics,
    )


from app.db.models.extraction_result import ExtractionResult


def _as_result_dict(result) -> dict:
    if result is None:
        return {}

    if isinstance(result, dict):
        return result

    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")

    if hasattr(result, "dict"):
        return result.dict()

    return {}


def _extract_raw_text(result_dict: dict) -> str | None:
    direct_keys = [
        "raw_text",
        "text",
        "ocr_text",
        "full_text",
        "extracted_text",
    ]

    for key in direct_keys:
        value = result_dict.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    lines: list[str] = []

    fields = result_dict.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict):
                name = field.get("name") or field.get("field_name") or field.get("key")
                value = field.get("value")
                if value not in (None, ""):
                    lines.append(f"{name}: {value}" if name else str(value))

    normalized = result_dict.get("normalized_data")
    if isinstance(normalized, dict):
        for key, value in normalized.items():
            if value not in (None, ""):
                lines.append(f"{key}: {value}")

    for key in ["ocr_results", "pages", "lines"]:
        value = result_dict.get(key)

        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    lines.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("value")
                    if isinstance(text, str) and text.strip():
                        lines.append(text.strip())

    return "\\n".join(lines) if lines else None


async def save_extraction_result_detail(
    db: AsyncSession,
    *,
    history_id: str | None,
    job_id: str | None,
    result,
) -> None:
    result_dict = _as_result_dict(result)

    existing_detail = None

    if job_id:
        existing = await db.execute(
            select(ExtractionResult).where(ExtractionResult.job_id == job_id)
        )
        existing_detail = existing.scalar_one_or_none()

    if existing_detail:
        existing_detail.history_id = history_id or existing_detail.history_id
        existing_detail.raw_text = _extract_raw_text(result_dict)
        existing_detail.result_json = result_dict or None
        existing_detail.fields_json = result_dict.get("fields") or result_dict.get("normalized_data")
        existing_detail.diagnostics_json = (
            result_dict.get("diagnostics")
            or result_dict.get("warnings")
            or result_dict.get("errors")
        )
    else:
        detail = ExtractionResult(
            history_id=history_id,
            job_id=job_id,
            raw_text=_extract_raw_text(result_dict),
            result_json=result_dict or None,
            fields_json=result_dict.get("fields") or result_dict.get("normalized_data"),
            diagnostics_json=(
                result_dict.get("diagnostics")
                or result_dict.get("warnings")
                or result_dict.get("errors")
            ),
        )
        db.add(detail)

    await db.commit()


async def _current_user_from_request(
    request: Request,
    db: AsyncSession,
) -> User:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")

    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Vous devez vous connecter pour lancer une extraction.",
        )

    token = auth.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Vous devez vous connecter pour lancer une extraction.",
        )

    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré.") from exc

    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    require_extract_permission(user)

    return user


def _user_id_from_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")

    if not auth or not auth.lower().startswith("bearer "):
        return None

    token = auth.split(" ", 1)[1].strip()

    if not token:
        return None

    try:
        from app.core.security import decode_access_token

        payload = decode_access_token(token)
        return str(payload.get("sub") or "") or None
    except Exception:
        return None


async def _save_history_safely(
    *,
    user_id: str | None,
    user_email: str | None,
    user_role: str | None,
    org_id: str | None,
    job_id: str | None,
    file_name: str | None,
    request: ExtractionRequest,
    result: ExtractionResponse,
) -> str | None:
    if not user_id:
        return None

    try:
        from app.db.session import AsyncSessionLocal
        from app.services.history_service import create_history_entry

        async with AsyncSessionLocal() as db:
            history = await create_history_entry(
                db,
                user_id=user_id,
                organization_id=org_id,
                user_email=user_email,      
                user_role=user_role,
                job_id=job_id,
                file_name=file_name,
                request=request,
                result=result,
            )

            if history is None and job_id:
                existing = await db.execute(
                    select(ExtractionHistory).where(ExtractionHistory.job_id == job_id)
                )
                history = existing.scalar_one_or_none()

            if history is not None:
                if hasattr(history, "user_email"):
                    history.user_email = user_email

                if hasattr(history, "user_role"):
                    history.user_role = user_role

                await db.commit()
                await db.refresh(history)

                return str(history.id)

            await db.commit()

    except Exception as exc:
        log.warning(
            "History save ignored",
            extra={
                "job_id": job_id,
                "user_id": user_id,
                "user_email": user_email,
                "error": str(exc),
            },
        )

    return None

def _safe_filename(filename: Optional[str]) -> str:
    name = Path(filename or "upload.png").name.strip()
    return name or "upload.png"


def _validate_upload_content(content: bytes, filename: Optional[str]) -> None:
    if not content:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file is empty: {filename or 'upload'}",
        )

    max_size_mb = 25
    max_size_bytes = max_size_mb * 1024 * 1024

    if len(content) > max_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Uploaded file too large: {len(content)} bytes. "
                f"Limit is {max_size_mb} MB."
            ),
        )


def _assert_saved_upload_readable(file_path: str) -> None:
    path = Path(file_path)

    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Upload save failed: file does not exist after save: {path}",
        )

    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Upload save failed: path is not a file: {path}",
        )

    size = path.stat().st_size

    if size <= 0:
        raise HTTPException(
            status_code=500,
            detail=f"Upload save failed: saved file is empty: {path}",
        )


def _guard_langs_for_type(document_type: str) -> list[str]:
    """
    Réduit le temps du guard.

    Principe:
    - On évite ar + fr + en pour tous les documents.
    - On garde quand même les langues nécessaires pour détecter les mauvais cas fréquents.
    """
    selected = normalize_document_type(document_type)

    if selected == "cin_tn":
        # ar pour CIN TN, en pour détecter une carte étrangère choisie comme CIN TN.
        return ["ar", "en"]

    if selected == "passport":
        # en pour passeport/carte étrangère, ar pour bloquer CIN TN choisie comme passeport.
        return ["en", "ar"]

    if selected == "id_document":
        # en pour cartes étrangères/passeports, ar pour accepter/détecter CIN TN.
        return ["en", "ar"]

    if selected == "invoice":
        # fr pour facture, ar pour bloquer CIN TN envoyée comme facture.
        return ["fr", "ar"]

    if selected == "registre_commerce":
        # fr pour registre, ar pour bloquer CIN TN envoyée comme registre.
        return ["fr", "ar"]

    return ["en"]


def _get_guard_ocr(lang: str):
    """
    Retourne une instance PaddleOCR cachee par langue.
    Cela réduit beaucoup le temps après la première requête.
    """
    cached = _GUARD_OCR_CACHE.get(lang)
    if cached is not None:
        return cached

    from paddleocr import PaddleOCR

    try:
        ocr = PaddleOCR(lang=lang, show_log=False, use_angle_cls=False)
    except TypeError:
        # Compatibilité PaddleOCR 3.x si certains paramètres changent.
        ocr = PaddleOCR(lang=lang)

    _GUARD_OCR_CACHE[lang] = ocr
    return ocr


def _ocr_preview_for_guard(file_path: str, document_type: str) -> str:
    """
    OCR rapide seulement pour valider le type documentaire.

    Optimisations:
    - Première page uniquement.
    - DPI réduit à 80.
    - Langues limitées selon le type choisi.
    - Instances PaddleOCR mises en cache.
    """
    try:
        from app.pipeline.io import load_file_as_pages
        import cv2

        pages = load_file_as_pages(file_path, dpi=80)

        if not pages:
            return ""

        image = pages[0]
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        langs = _guard_langs_for_type(document_type)
        all_texts: list[str] = []

        def extract_texts_from_paddle(result) -> list[str]:
            texts: list[str] = []

            def walk(obj):
                if obj is None:
                    return

                if isinstance(obj, str):
                    texts.append(obj)
                    return

                if isinstance(obj, dict):
                    for key in ("text", "transcription", "rec_text"):
                        value = obj.get(key)
                        if isinstance(value, str):
                            texts.append(value)

                    for value in obj.values():
                        walk(value)

                    return

                if isinstance(obj, (list, tuple)):
                    # Format classique PaddleOCR: [box, (text, score)]
                    if len(obj) >= 2 and isinstance(obj[1], (list, tuple)) and obj[1]:
                        if isinstance(obj[1][0], str):
                            texts.append(obj[1][0])

                    for item in obj:
                        walk(item)

            walk(result)
            return texts

        for lang in langs:
            try:
                ocr = _get_guard_ocr(lang)
                result = ocr.ocr(rgb)
                lang_texts = extract_texts_from_paddle(result)

                preview_lang = " ".join(
                    t.strip() for t in lang_texts if t and t.strip()
                )

                log.info(
                    "Document type guard OCR preview lang",
                    extra={
                        "file_path": file_path,
                        "requested_document_type": document_type,
                        "paddle_lang": lang,
                        "preview_len": len(preview_lang),
                        "preview_sample": preview_lang[:250],
                    },
                )

                if preview_lang:
                    all_texts.append(preview_lang)

            except Exception as lang_exc:
                log.warning(
                    "Document type guard OCR lang failed",
                    extra={
                        "file_path": file_path,
                        "requested_document_type": document_type,
                        "paddle_lang": lang,
                        "error": str(lang_exc),
                    },
                )

        preview = " ".join(all_texts).strip()

        log.info(
            "Document type guard OCR preview merged",
            extra={
                "file_path": file_path,
                "document_type": document_type,
                "preview_len": len(preview),
                "preview_sample": preview[:500],
            },
        )

        return preview

    except Exception as exc:
        log.warning(
            "Document type guard OCR preview failed",
            extra={
                "file_path": file_path,
                "document_type": document_type,
                "error": str(exc),
            },
        )
        return ""


def _run_document_type_guard(file_path: str, request: ExtractionRequest) -> None:
    """
    Bloque l'extraction si le type sélectionné est clairement incompatible
    avec le document uploadé.
    """
    selected_type = normalize_document_type(getattr(request, "document_type", "auto"))

    # Ne pas bloquer les modes de test/template libre.
    if selected_type in {"auto", "custom", "unknown", ""}:
        return

    raw_text_preview = _ocr_preview_for_guard(
        file_path=file_path,
        document_type=selected_type,
    )

    detected = detect_document_type_from_text(raw_text_preview)

    log.info(
        "Document type guard raw preview analysis",
        extra={
            "selected_type": selected_type,
            "raw_preview_len": len(raw_text_preview or ""),
            "raw_preview_sample": (raw_text_preview or "")[:600],
            "detected_type": detected.detected_type,
            "confidence": detected.confidence,
            "reasons": detected.reasons[:10],
        },
    )

    compatible, reason = is_type_compatible(
        selected_type=selected_type,
        detected=detected,
        min_confidence_to_block=0.25,
    )

    log.info(
        "Document type guard",
        extra={
            "selected_type": selected_type,
            "detected_type": detected.detected_type,
            "confidence": detected.confidence,
            "compatible": compatible,
            "reason": reason,
            "reasons": detected.reasons[:8],
        },
    )

    if not compatible:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "DOCUMENT_TYPE_MISMATCH",
                "message": "Le type de document sélectionné ne correspond pas au document uploadé.",
                "selected_type": selected_type,
                "detected_type": detected.detected_type,
                "confidence": detected.confidence,
                "reasons": detected.reasons,
                "recommendation": f"Choisissez le type '{detected.detected_type}' ou utilisez 'auto'.",
            },
        )


@router.post(
    "/sync",
    response_model=ExtractionResponse,
    summary="Synchronous OCR extraction alias",
    description="Compatibility alias for POST /extract.",
)
@router.post(
    "",
    response_model=ExtractionResponse,
    summary="Synchronous OCR extraction",
    description="Upload a document and get structured extraction immediately.",
)
async def extract_sync(
    http_request: Request,
    tenant: TenantDep,
    file: UploadFile = File(...),
    request: ExtractionRequest = Depends(_parse_request),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    current_user = await _current_user_from_request(http_request, db)
    tenant.check_quota_jobs()

    job_id = str(uuid.uuid4())
    file_path: Optional[str] = None
    safe_name = _safe_filename(file.filename)
    content: bytes = b""
    audit_svc = get_audit_service()
    settings = get_settings()

    try:
        content = await file.read()
        _validate_upload_content(content, safe_name)

        file_path = storage.save_upload(
            content,
            safe_name,
            org_id=tenant.org_id,
        )

        _assert_saved_upload_readable(file_path)
        _run_document_type_guard(file_path, request)

        if settings.ENABLE_AUDIT_LOG:
            await audit_svc.extraction_started(
                tenant.org_id,
                job_id,
                request.template_id,
                tenant.raw_key_prefix,
            )

        engine_name = request.engine or "paddle"
        cb_key = engine_name if engine_name != "auto" else "paddle"
        cb = get_circuit_breaker(cb_key)

        if not cb.is_available():
            raise HTTPException(
                status_code=503,
                detail=f"Engine '{engine_name}' temporarily unavailable; circuit open",
            )

        try:
            result = get_ocr_service().extract_sync(
                file_path=file_path,
                request=request,
                job_id=job_id,
            )

            cb.record_success()

            history_id = await _save_history_safely(
                user_id=str(current_user.id),
                user_email=current_user.email,
                user_role=current_user.role,
                org_id=tenant.org_id,
                job_id=job_id,
                file_name=file.filename,
                request=request,
                result=result,
            )

            await save_extraction_result_detail(
                db,
                history_id=history_id,
                job_id=job_id,
                result=result,
            )

        except HTTPException:
            cb.record_failure()
            raise

        except Exception as exc:
            cb.record_failure()

            log.error(
                "Extraction sync failed",
                extra={
                    "job_id": job_id,
                    "error": str(exc),
                    "file_path": file_path,
                    "upload_filename": safe_name,
                    "content_size": len(content),
                    "document_type": getattr(request, "document_type", None),
                    "template_id": getattr(request, "template_id", None),
                    "ocr_engine": getattr(request, "engine", None),
                },
            )

            raise HTTPException(
                status_code=500,
                detail=(
                    f"Extraction failed: {exc}. "
                    f"Saved file: {file_path}. "
                    f"Upload filename: {safe_name}. "
                    f"Size: {len(content)} bytes."
                ),
            ) from exc

        if settings.ENABLE_QUOTA_DB_UPDATE:
            try:
                from app.db.session import AsyncSessionLocal
                from app.services.tenant_service import TenantService

                async with AsyncSessionLocal() as db:
                    await TenantService(db).increment_usage(tenant.org_id, pages=1)
                    await db.commit()

            except Exception as quota_exc:
                log.warning(
                    "Quota DB update ignore",
                    extra={
                        "error": str(quota_exc),
                        "org_id": tenant.org_id,
                    },
                )

        try:
            from app.core.metrics import record_extraction

            record_extraction(
                org_slug=tenant.org_slug,
                template_id=result.template_id,
                engine=result.engine_used,
                status=result.status,
                confidence=result.global_confidence,
                duration_seconds=result.processing_time_ms / 1000,
                page_count=1,
                fields=result.fields,
            )

        except Exception:
            pass

        if settings.ENABLE_AUDIT_LOG:
            await audit_svc.extraction_done(
                tenant.org_id,
                job_id,
                result.status,
                result.global_confidence,
            )

        return result

    except HTTPException:
        raise

    except Exception as exc:
        log.error(
            "Extraction route failed before OCR",
            extra={
                "job_id": job_id,
                "error": str(exc),
                "file_path": file_path,
                "upload_filename": safe_name,
                "content_size": len(content) if content else 0,
                "document_type": getattr(request, "document_type", None),
                "template_id": getattr(request, "template_id", None),
            },
        )

        raise HTTPException(
            status_code=500,
            detail=f"Extraction failed: {exc}",
        ) from exc

    finally:
        if file_path:
            try:
                storage.delete_upload(file_path)
            except Exception as cleanup_exc:
                log.warning(
                    "Upload cleanup failed",
                    extra={
                        "file_path": file_path,
                        "error": str(cleanup_exc),
                    },
                )


@router.post(
    "/async",
    response_model=SuccessResponse[dict],
    status_code=202,
    summary="Async OCR extraction",
)
async def extract_async(
    http_request: Request,
    tenant: TenantDep,
    file: UploadFile = File(...),
    request: ExtractionRequest = Depends(_parse_request),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    current_user = await _current_user_from_request(http_request, db)
    tenant.check_quota_jobs()

    content = await file.read()
    safe_name = _safe_filename(file.filename)
    _validate_upload_content(content, safe_name)

    file_path = storage.save_upload(
        content,
        safe_name,
        org_id=tenant.org_id,
    )

    _assert_saved_upload_readable(file_path)
    _run_document_type_guard(file_path, request)

    job_id = str(uuid.uuid4())
    db_job_created = False

    try:
        from app.db.session import AsyncSessionLocal
        from app.services.job_service import JobService

        async with AsyncSessionLocal() as db:
            job = await JobService(db).create(
                org_id=tenant.org_id,
                api_key_id=tenant.api_key.id,
                template_id=request.template_id,
                file_name=safe_name,
                file_size_bytes=len(content),
                webhook_url=str(request.webhook_url) if request.webhook_url else None,
            )
            job_id = job.id
            await db.commit()
            db_job_created = True

    except Exception as db_exc:
        log.warning(
            "Job DB not created",
            extra={
                "error": str(db_exc),
                "job_id": job_id,
                "upload_filename": safe_name,
            },
        )

    await get_audit_service().extraction_started(
        tenant.org_id,
        job_id,
        request.template_id,
        tenant.raw_key_prefix,
    )

    dispatched_celery = False

    try:
        from app.worker.tasks import run_ocr_task

        task = run_ocr_task.apply_async(
            kwargs={
                "job_id": job_id,
                "org_id": tenant.org_id,
                "file_path": file_path,
                "request_data": request.model_dump(mode="json"),
            },
            queue="ocr",
        )

        if db_job_created:
            try:
                from app.db.session import AsyncSessionLocal
                from app.services.job_service import JobService

                async with AsyncSessionLocal() as db:
                    await JobService(db).update(
                        job_id,
                        tenant.org_id,
                        celery_task_id=task.id,
                    )
                    await db.commit()
            except Exception:
                pass

        dispatched_celery = True

        log.info(
            "Job Celery dispatched",
            extra={
                "job_id": job_id,
                "task_id": task.id,
            },
        )

    except Exception as celery_exc:
        log.warning(
            "Celery unavailable, asyncio fallback",
            extra={
                "error": str(celery_exc),
                "job_id": job_id,
            },
        )

    if not dispatched_celery:
        import asyncio

        asyncio.create_task(
            _run_extraction_fallback(
                job_id=job_id,
                org_id=tenant.org_id,
                file_path=file_path,
                request=request,
            )
        )

    return SuccessResponse(
        data={
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/jobs/{job_id}",
            "organisation": tenant.org_slug,
            "backend": "celery" if dispatched_celery else "asyncio",
        }
    )


async def _run_extraction_fallback(
    job_id: str,
    org_id: str,
    file_path: str,
    request: ExtractionRequest,
) -> None:
    from app.services.document_orchestrator import DocumentOrchestrator
    from app.services.storage_service import get_storage_service

    storage = get_storage_service()

    try:
        from app.db.session import AsyncSessionLocal
        from app.services.job_service import JobService

        async with AsyncSessionLocal() as db:
            await JobService(db).update(
                job_id,
                org_id,
                status="processing",
                progress_pct=10,
            )
            await db.commit()

    except Exception:
        pass

    try:
        orchestrator = DocumentOrchestrator()
        result = orchestrator.process(
            file_path=file_path,
            request=request,
            job_id=job_id,
        )

        result_dict = result.model_dump(mode="json")
        result_path = storage.save_result(
            job_id,
            result_dict,
            org_id=org_id,
        )

        try:
            from app.db.session import AsyncSessionLocal
            from app.services.job_service import JobService

            async with AsyncSessionLocal() as db:
                await JobService(db).update(
                    job_id,
                    org_id,
                    status="done",
                    progress_pct=100,
                    result_path=result_path,
                    global_confidence=result.global_confidence,
                    field_count=len(result.fields),
                    processing_time_ms=result.processing_time_ms,
                    engine_used=result.engine_used,
                )
                await db.commit()

        except Exception as db_exc:
            log.warning(
                "Job DB update ignored",
                extra={
                    "job_id": job_id,
                    "error": str(db_exc),
                },
            )

        if request.webhook_url:
            from app.services.webhook_service import send_webhook

            await send_webhook(str(request.webhook_url), result_dict)

    except Exception as exc:
        log.error(
            "Fallback extraction failed",
            extra={
                "job_id": job_id,
                "error": str(exc),
                "file_path": file_path,
            },
        )

        try:
            from app.db.session import AsyncSessionLocal
            from app.services.job_service import JobService

            async with AsyncSessionLocal() as db:
                await JobService(db).update(
                    job_id,
                    org_id,
                    status="failed",
                    error=str(exc),
                )
                await db.commit()

        except Exception:
            pass

    finally:
        try:
            storage.delete_upload(file_path)
        except Exception:
            pass
