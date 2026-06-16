"""app/routers/routes_dashboard.py - Dashboard statistics."""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return authorization.split(" ", 1)[1].strip()

async def _current_user(authorization: Optional[str] = Header(None, alias="Authorization"), db: AsyncSession = Depends(get_db)) -> User:
    payload = decode_access_token(_extract_bearer_token(authorization))
    result = await db.execute(select(User).where(User.id == str(payload.get("sub"))))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user

@router.get("/stats")
async def dashboard_stats(current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    is_admin = bool(current.is_superuser or current.role == "admin")
    users_total = users_active = users_admin = 0
    if is_admin:
        users_total = int((await db.execute(select(func.count()).select_from(User))).scalar() or 0)
        users_active = int((await db.execute(select(func.count()).select_from(User).where(User.is_active == True))).scalar() or 0)
        users_admin = int((await db.execute(select(func.count()).select_from(User).where(User.role == "admin"))).scalar() or 0)
    stats = {"scope": "global" if is_admin else "personal", "users": {"total": users_total, "active": users_active, "admins": users_admin}, "extractions": {"total": 0, "success": 0, "review_required": 0, "failed": 0, "avg_confidence": None, "avg_processing_time_ms": None, "by_document_type": [], "recent": []}}
    try:
        from app.db.models.extraction_history import ExtractionHistory
        base_filter = [] if is_admin else [ExtractionHistory.user_id == current.id]
        total_q = select(func.count()).select_from(ExtractionHistory)
        if base_filter: total_q = total_q.where(*base_filter)
        total = int((await db.execute(total_q)).scalar() or 0)
        async def count_status(st: str) -> int:
            q = select(func.count()).select_from(ExtractionHistory).where(ExtractionHistory.status == st)
            if base_filter: q = q.where(*base_filter)
            return int((await db.execute(q)).scalar() or 0)
        avg_conf_q = select(func.avg(ExtractionHistory.global_confidence)); avg_time_q = select(func.avg(ExtractionHistory.processing_time_ms))
        if base_filter:
            avg_conf_q = avg_conf_q.where(*base_filter); avg_time_q = avg_time_q.where(*base_filter)
        avg_conf = (await db.execute(avg_conf_q)).scalar(); avg_time = (await db.execute(avg_time_q)).scalar()
        by_doc_q = select(ExtractionHistory.document_type, func.count()).group_by(ExtractionHistory.document_type).order_by(func.count().desc())
        if base_filter: by_doc_q = by_doc_q.where(*base_filter)
        by_doc_rows = (await db.execute(by_doc_q)).all()
        recent_q = select(ExtractionHistory).order_by(ExtractionHistory.created_at.desc()).limit(8)
        if base_filter: recent_q = recent_q.where(*base_filter)
        recent_rows = (await db.execute(recent_q)).scalars().all()
        stats["extractions"] = {"total": total, "success": await count_status("success"), "review_required": await count_status("review_required"), "failed": await count_status("failed"), "avg_confidence": round(float(avg_conf), 4) if avg_conf is not None else None, "avg_processing_time_ms": int(avg_time) if avg_time is not None else None, "by_document_type": [{"document_type": doc or "unknown", "count": int(count)} for doc, count in by_doc_rows], "recent": [{"id": r.id, "file_name": r.file_name, "document_type": r.document_type, "status": r.status, "global_confidence": r.global_confidence, "processing_time_ms": r.processing_time_ms, "created_at": r.created_at.isoformat() if r.created_at else None} for r in recent_rows]}
    except Exception as exc:
        stats["extractions"]["error"] = f"history_unavailable: {exc}"
    return stats