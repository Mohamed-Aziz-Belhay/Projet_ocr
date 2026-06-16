from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.extraction_history import ExtractionHistory
from app.db.models.extraction_result import ExtractionResult
from app.db.models.user import User
from app.db.session import get_db
from app.services.assistant_service import get_assistant_service
from app.services.llm_provider import get_llm_provider, reset_llm_provider


router = APIRouter(prefix="/assistant", tags=["Assistant OCR"])


class AssistantChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    last_result: Optional[Dict[str, Any]] = None
    use_ai: bool = False
    include_history: bool = True


class AssistantChatResponse(BaseModel):
    reply: str
    suggestions: List[str] = []
    severity: str = "info"
    mode: str = "rules"
    debug: Optional[Dict[str, Any]] = None


async def _current_user_from_request(
    request: Request,
    db: AsyncSession,
) -> User:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")

    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Vous devez vous connecter pour utiliser l’assistant.",
        )

    token = auth.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Token manquant.")

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

    role = str(getattr(user, "role", "") or "").lower()
    is_superuser = bool(getattr(user, "is_superuser", False))

    if not is_superuser and role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=403,
            detail="Votre rôle ne permet pas d’utiliser l’assistant OCR.",
        )

    return user


def _safe_json_load(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value

    return value


def _history_to_dict(item: ExtractionHistory) -> Dict[str, Any]:
    return {
        "id": getattr(item, "id", None),
        "job_id": getattr(item, "job_id", None),
        "user_id": getattr(item, "user_id", None),
        "user_email": getattr(item, "user_email", None),
        "user_role": getattr(item, "user_role", None),
        "file_name": getattr(item, "file_name", None),
        "document_type": getattr(item, "document_type", None),
        "template_id": getattr(item, "template_id", None),
        "engine_used": getattr(item, "engine_used", None),
        "status": getattr(item, "status", None),
        "global_confidence": getattr(item, "global_confidence", None),
        "processing_time_ms": getattr(item, "processing_time_ms", None),
        "field_count": getattr(item, "field_count", None),
        "created_at": str(getattr(item, "created_at", "")) if getattr(item, "created_at", None) else None,
    }


async def _load_history_for_user(
    *,
    db: AsyncSession,
    user: User,
    limit: int = 20,
) -> List[ExtractionHistory]:
    role = str(getattr(user, "role", "") or "").lower()
    is_superuser = bool(getattr(user, "is_superuser", False))

    stmt = (
        select(ExtractionHistory)
        .order_by(ExtractionHistory.created_at.desc())
        .limit(limit)
    )

    if not is_superuser and role != "admin":
        stmt = (
            select(ExtractionHistory)
            .where(ExtractionHistory.user_id == str(user.id))
            .order_by(ExtractionHistory.created_at.desc())
            .limit(limit)
        )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _load_latest_result_detail(
    *,
    db: AsyncSession,
    latest_history: Optional[ExtractionHistory],
) -> Optional[Dict[str, Any]]:
    if latest_history is None:
        return None

    history_id = str(getattr(latest_history, "id", "") or "")
    job_id = getattr(latest_history, "job_id", None)

    conditions = []

    if history_id:
        conditions.append(ExtractionResult.history_id == history_id)

    if job_id:
        conditions.append(ExtractionResult.job_id == job_id)

    if not conditions:
        return None

    stmt = (
        select(ExtractionResult)
        .where(or_(*conditions))
        .order_by(ExtractionResult.created_at.desc())
        .limit(1)
    )

    result = await db.execute(stmt)
    detail = result.scalar_one_or_none()

    if not detail:
        fallback = _safe_json_load(getattr(latest_history, "result_json", None))
        return fallback if isinstance(fallback, dict) else None

    result_json = _safe_json_load(getattr(detail, "result_json", None))
    fields_json = _safe_json_load(getattr(detail, "fields_json", None))
    diagnostics_json = _safe_json_load(getattr(detail, "diagnostics_json", None))

    if isinstance(result_json, dict):
        result_dict = result_json
    else:
        result_dict = {}

    if fields_json is not None and "fields" not in result_dict:
        result_dict["fields"] = fields_json

    if diagnostics_json is not None and "diagnostics" not in result_dict:
        result_dict["diagnostics"] = diagnostics_json

    raw_text = getattr(detail, "raw_text", None)

    if raw_text and "raw_text" not in result_dict:
        result_dict["raw_text"] = raw_text

    result_dict["_history"] = _history_to_dict(latest_history)

    return result_dict


@router.get("/status")
async def assistant_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _current_user_from_request(request, db)

    reset_llm_provider()
    provider = get_llm_provider()

    return {
        "assistant": "ok",
        "llm": provider.status(),
    }


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    payload: AssistantChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await _current_user_from_request(request, db)

    history_rows: List[ExtractionHistory] = []
    history_dicts: List[Dict[str, Any]] = []
    latest_result_from_history: Optional[Dict[str, Any]] = None

    if payload.include_history:
        history_rows = await _load_history_for_user(
            db=db,
            user=user,
            limit=20,
        )

        history_dicts = [_history_to_dict(row) for row in history_rows]

        latest_history = history_rows[0] if history_rows else None

        latest_result_from_history = await _load_latest_result_detail(
            db=db,
            latest_history=latest_history,
        )

    response = await get_assistant_service().chat(
        message=payload.message,
        last_result=payload.last_result,
        latest_result_from_history=latest_result_from_history,
        history=history_dicts,
        use_ai=payload.use_ai,
    )

    debug = response.get("debug") or {}
    debug.update(
        {
            "payload_use_ai": payload.use_ai,
            "payload_include_history": payload.include_history,
            "history_count": len(history_dicts),
            "has_latest_result_from_history": latest_result_from_history is not None,
            "has_payload_last_result": payload.last_result is not None,
        }
    )

    response["debug"] = debug

    return AssistantChatResponse(**response)