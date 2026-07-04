"""
app/services/history_service.py
Trace extraction results per authenticated user.
"""
from __future__ import annotations
import json
from typing import Any, Dict, List, Optional

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


def snake_to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


def build_normalized_data(fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {snake_to_camel(f["name"]): f.get("value") for f in fields if f.get("name")}


def apply_corrections(
    fields: Optional[List[Dict[str, Any]]],
    corrections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Apply user-submitted field corrections onto a stored fields_json list.

    Every field is marked validated=True/review_required=False (the human has
    reviewed the whole extraction, not just the fields they touched). Only
    fields present in `corrections` with a value actually different from the
    current one get their value overwritten; for those, `original_value` is
    set to the true OCR value (only on the first correction — a later
    correction round must not overwrite it with an already-corrected value)
    and `corrected` is set to True.
    """
    by_name = {c["name"]: c["value"] for c in corrections if c.get("name")}

    updated: List[Dict[str, Any]] = []
    for field in fields or []:
        field = dict(field)
        name = field.get("name")
        if name in by_name and by_name[name] != field.get("value"):
            if not field.get("corrected"):
                field["original_value"] = field.get("value")
            field["value"] = by_name[name]
            field["corrected"] = True
            # error/reasons described why the OLD (now discarded) value was
            # rejected (e.g. "Lieu de naissance générique/suspect"); keeping
            # them would misleadingly flag a value the user just confirmed
            # is correct.
            field["error"] = None
            field["reasons"] = ["user_corrected"]
        field["validated"] = True
        field["review_required"] = False
        updated.append(field)
    return updated