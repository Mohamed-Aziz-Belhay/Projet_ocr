"""
app/services/history_service.py
Trace extraction results per authenticated user.
"""
from __future__ import annotations
import json
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.extraction_history import ExtractionHistory


def _safe_json(value: Any) -> str:
    try:
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


async def create_history_entry(
    db: AsyncSession,
    *,
    user_id: Optional[str],
    user_email: Optional[str] = None,
    user_role: Optional[str] = None,
    organization_id: Optional[str],
    job_id: Optional[str],
    file_name: Optional[str],
    request: Any,
    result: Any,
) -> ExtractionHistory:
    fields = getattr(result, "fields", []) or []

    kwargs: dict = dict(
        user_id=user_id,
        organization_id=organization_id,
        job_id=job_id,
        file_name=file_name,
        document_type=getattr(request, "document_type", None),
        template_id=getattr(result, "template_id", None) or getattr(request, "template_id", None),
        engine_used=getattr(result, "engine_used", None) or getattr(request, "engine", None),
        status=getattr(result, "status", None),
        global_confidence=getattr(result, "global_confidence", None),
        processing_time_ms=getattr(result, "processing_time_ms", None),
        field_count=len(fields),
        result_json=_safe_json(result),
    )

    # Ajoute user_email et user_role seulement si le modèle les supporte
    if hasattr(ExtractionHistory, "user_email"):
        kwargs["user_email"] = user_email
    if hasattr(ExtractionHistory, "user_role"):
        kwargs["user_role"] = user_role

    row = ExtractionHistory(**kwargs)
    db.add(row)
    await db.flush()
    return row


def history_to_dict(row: ExtractionHistory) -> dict:
    return {
        "id":                 str(getattr(row, "id", "") or ""),
        "job_id":             getattr(row, "job_id", None),
        "user_id":            getattr(row, "user_id", None),
        "user_email":         getattr(row, "user_email", None),
        "user_role":          getattr(row, "user_role", None),
        "organization_id":    getattr(row, "organization_id", None),
        "file_name":          getattr(row, "file_name", None),
        "document_type":      getattr(row, "document_type", None),
        "template_id":        getattr(row, "template_id", None),
        "engine_used":        getattr(row, "engine_used", None),
        "status":             getattr(row, "status", None),
        "global_confidence":  getattr(row, "global_confidence", None),
        "processing_time_ms": getattr(row, "processing_time_ms", None),
        "field_count":        getattr(row, "field_count", None),
        "created_at":         row.created_at.isoformat() if getattr(row, "created_at", None) else None,
    }


async def list_history_for_user(
    db: AsyncSession, *, user_id: str, limit: int = 50
) -> list[dict]:
    result = await db.execute(
        select(ExtractionHistory)
        .where(ExtractionHistory.user_id == user_id)
        .order_by(desc(ExtractionHistory.created_at))
        .limit(limit)
    )
    return [history_to_dict(row) for row in result.scalars().all()]


async def list_all_history(db: AsyncSession, *, limit: int = 100) -> list[dict]:
    result = await db.execute(
        select(ExtractionHistory)
        .order_by(desc(ExtractionHistory.created_at))
        .limit(limit)
    )
    return [history_to_dict(row) for row in result.scalars().all()]