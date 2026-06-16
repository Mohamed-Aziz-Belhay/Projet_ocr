#routers/routes_scanner.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.core.rbac import require_extract_permission
from app.db.models.user import User
from app.db.session import get_db
from app.services.scanner_session_service import (
    clear_active_scanner_user,
    get_active_scanner_user,
    set_active_scanner_user,
)

router = APIRouter(prefix="/scanner", tags=["Scanner"])


async def _current_user_from_request(
    request: Request,
    db: AsyncSession,
) -> User:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")

    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Vous devez vous connecter pour activer le scanner.",
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

    require_extract_permission(user)

    return user


@router.post("/session/claim")
async def claim_scanner_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    L'utilisateur connecté devient l'utilisateur associé
    aux scans automatiques.
    """
    user = await _current_user_from_request(request, db)

    payload = set_active_scanner_user(
        user_id=str(user.id),
        user_email=getattr(user, "email", None),
        user_role=str(getattr(user, "role", "") or ""),
        organization_id=getattr(user, "organization_id", None),
        full_name=getattr(user, "full_name", None),
    )

    return {
        "ok": True,
        "message": "Session scanner associée à l'utilisateur connecté.",
        "scanner_user": payload,
    }


@router.get("/session/current")
def current_scanner_session():
    user = get_active_scanner_user()

    return {
        "active": user is not None,
        "scanner_user": user,
    }


@router.post("/session/release")
def release_scanner_session():
    clear_active_scanner_user()

    return {
        "ok": True,
        "message": "Session scanner libérée.",
    }