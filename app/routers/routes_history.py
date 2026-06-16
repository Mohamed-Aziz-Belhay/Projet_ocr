"""
app/routers/routes_history.py
Admin voit tout. Operator voit le sien. Simple_user voit le sien.
Admin peut supprimer.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.extraction_history import ExtractionHistory
from app.db.models.extraction_result import ExtractionResult
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/history", tags=["History"])


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


async def _current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_bearer_token(authorization)
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")
    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive")
    return user


def _is_admin(user: User) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "role", "") == "admin")

def _is_operator(user: User) -> bool:
    return getattr(user, "role", "") == "operator"

def _is_simple_user(user: User) -> bool:
    return getattr(user, "role", "") == "simple_user"


def _history_to_dict(item: ExtractionHistory) -> dict:
    return {
        "id":                 str(getattr(item, "id", "")),
        "job_id":             getattr(item, "job_id", None),
        "user_id":            getattr(item, "user_id", None),
        "user_email":         getattr(item, "user_email", None),
        "user_role":          getattr(item, "user_role", None),
        "organization_id":    getattr(item, "organization_id", None),
        "file_name":          getattr(item, "file_name", None),
        "document_type":      getattr(item, "document_type", None),
        "template_id":        getattr(item, "template_id", None),
        "engine_used":        getattr(item, "engine_used", None),
        "status":             getattr(item, "status", None),
        "global_confidence":  getattr(item, "global_confidence", None),
        "processing_time_ms": getattr(item, "processing_time_ms", None),
        "field_count":        getattr(item, "field_count", None),
        "created_at":         item.created_at.isoformat() if getattr(item, "created_at", None) else None,
    }


def _own_conditions(user: User):
    """Retourne les conditions SQLAlchemy pour filtrer par utilisateur."""
    conditions = []
    if hasattr(ExtractionHistory, "user_id"):
        conditions.append(ExtractionHistory.user_id == str(user.id))
    if hasattr(ExtractionHistory, "user_email"):
        conditions.append(ExtractionHistory.user_email == user.email)
    return conditions


@router.get("")
async def list_history(
    limit: int = 100,
    current: User = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
):
    max_limit = min(limit, 500)

    if _is_admin(current):
        # Admin : tout voir
        stmt = (
            select(ExtractionHistory)
            .order_by(ExtractionHistory.created_at.desc())
            .limit(max_limit)
        )
    elif _is_operator(current) or _is_simple_user(current):
        # Operator et simple_user : seulement leur propre historique
        conditions = _own_conditions(current)
        if not conditions:
            return {"items": []}
        stmt = (
            select(ExtractionHistory)
            .where(or_(*conditions))
            .order_by(ExtractionHistory.created_at.desc())
            .limit(max_limit)
        )
    else:
        raise HTTPException(status_code=403, detail="Accès non autorisé")

    result = await db.execute(stmt)
    return {"items": [_history_to_dict(item) for item in result.scalars().all()]}


@router.get("/{id_or_job_id}")
async def get_history_detail(
    id_or_job_id: str,
    current: User = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ExtractionHistory).where(
            or_(
                ExtractionHistory.id == id_or_job_id,
                ExtractionHistory.job_id == id_or_job_id,
            )
        )
    )
    history = result.scalar_one_or_none()

    if not history:
        raise HTTPException(status_code=404, detail="History item not found")

    # Vérifier accès : admin voit tout, les autres voient seulement le leur
    if not _is_admin(current):
        own = False
        if hasattr(history, "user_id"):
            own = own or getattr(history, "user_id", None) == str(current.id)
        if hasattr(history, "user_email"):
            own = own or getattr(history, "user_email", None) == current.email
        if not own:
            raise HTTPException(status_code=403, detail="Accès refusé à ce détail")

    detail_result = await db.execute(
        select(ExtractionResult).where(
            or_(
                ExtractionResult.history_id == str(history.id),
                ExtractionResult.job_id == history.job_id,
            )
        )
    )
    detail = detail_result.scalar_one_or_none()

    return {
        "history":        _history_to_dict(history),
        "raw_text":       getattr(detail, "raw_text", None) if detail else None,
        "result_json":    getattr(detail, "result_json", None) if detail else None,
        "fields_json":    getattr(detail, "fields_json", None) if detail else None,
        "diagnostics_json": getattr(detail, "diagnostics_json", None) if detail else None,
        "created_at":     detail.created_at.isoformat() if detail and detail.created_at else None,
    }


@router.delete("/{id_or_job_id}")
async def delete_history_item(
    id_or_job_id: str,
    current: User = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _is_admin(current):
        raise HTTPException(status_code=403, detail="Suppression réservée à l'admin")

    result = await db.execute(
        select(ExtractionHistory).where(
            or_(
                ExtractionHistory.id == id_or_job_id,
                ExtractionHistory.job_id == id_or_job_id,
            )
        )
    )
    history = result.scalar_one_or_none()
    if not history:
        raise HTTPException(status_code=404, detail="History item not found")

    detail_result = await db.execute(
        select(ExtractionResult).where(
            or_(
                ExtractionResult.history_id == str(history.id),
                ExtractionResult.job_id == history.job_id,
            )
        )
    )
    for detail in detail_result.scalars().all():
        await db.delete(detail)

    history_id = str(history.id)
    job_id = history.job_id
    await db.delete(history)
    await db.commit()

    return {"ok": True, "id": history_id, "job_id": job_id}