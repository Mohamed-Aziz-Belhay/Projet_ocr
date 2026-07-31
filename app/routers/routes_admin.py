"""
app/routers/routes_admin.py
Admin endpoints — runtime config, engine status, OCR profiles.

[SÉCURITÉ #4] Défense en profondeur (audit de sécurité complémentaire) :
Ce router ne vérifie plus uniquement le scope d'un contexte tenant (qui
pouvait auparavant être obtenu via une clé API partagée entre tous les
rôles), mais exige EN PLUS que le rôle JWT réel de l'utilisateur connecté
soit "admin" (ou is_superuser). _require_admin() combine désormais :
  1. require_admin(user)      -> vérifie le rôle réel de l'utilisateur JWT
  2. tenant.require_scope()   -> vérifie le scope dérivé (get_org_context_for_user)
Les deux couches doivent être satisfaites ; aucune des deux ne suffit seule.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.runtime import get_runtime_config, RuntimeConfig
from app.config.ocr_profiles import list_profiles, get_profile, PROFILES
from app.engines.engine_factory import available_engines
from app.schemas.responses import SuccessResponse
from app.core.tenant import TenantContext, get_org_context_for_user
from app.core.errors import OCRServiceError
from app.core.security import decode_access_token
from app.core.rbac import require_admin
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["Admin"])


async def _get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")

    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Vous devez vous connecter.")

    token = auth.split(" ", 1)[1].strip()

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

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Votre compte est en attente de validation par l'admin.")

    return user


async def _require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """
    [SÉCURITÉ #4] Double vérification avant tout accès aux routes admin :
    - require_admin(user) : lève 403 si le rôle JWT réel n'est pas admin
      (ni is_superuser).
    - tenant.require_scope("admin") : lève 403 si le scope dérivé du rôle
      (via get_org_context_for_user) ne contient pas "admin".
    Remplace l'ancienne dépendance qui ne vérifiait qu'un scope de tenant,
    potentiellement obtenu via une clé API partagée entre tous les rôles.
    """
    user = await _get_current_user(request, db)
    require_admin(user)

    tenant = await get_org_context_for_user(user, db)
    tenant.require_scope("admin")

    return tenant


# ── Runtime config ─────────────────────────────────────────────────────────────

@router.get("/config", response_model=SuccessResponse[dict])
async def get_config(tenant: TenantContext = Depends(_require_admin)):
    return SuccessResponse(data=get_runtime_config().get_all())


@router.patch("/config", response_model=SuccessResponse[dict])
async def update_config(
    updates: Dict[str, Any] = Body(...),
    tenant:  TenantContext = Depends(_require_admin),
):
    try:
        get_runtime_config().set_many(updates)
    except ValueError as exc:
        raise OCRServiceError(str(exc)) from exc
    return SuccessResponse(data=get_runtime_config().get_all())


@router.post("/config/reset", response_model=SuccessResponse[dict])
async def reset_config(tenant: TenantContext = Depends(_require_admin)):
    get_runtime_config().reset()
    return SuccessResponse(data=get_runtime_config().get_all())


# ── Maintenance ────────────────────────────────────────────────────────────────

@router.post("/maintenance", response_model=SuccessResponse[dict])
async def toggle_maintenance(
    enabled: bool = Body(..., embed=True),
    message: str  = Body("Service temporarily unavailable", embed=True),
    tenant:  TenantContext = Depends(_require_admin),
):
    get_runtime_config().set_many({"maintenance_mode": enabled, "maintenance_message": message})
    return SuccessResponse(data={"maintenance_mode": enabled, "message": message})


# ── Engine status ──────────────────────────────────────────────────────────────

@router.get("/engines", response_model=SuccessResponse[dict])
async def engine_status(tenant: TenantContext = Depends(_require_admin)):
    engines = available_engines()
    from app.engines.circuit_breaker import all_breaker_states
    return SuccessResponse(data={
        "engines":          engines,
        "circuit_breakers": all_breaker_states(),
        "available_count":  sum(1 for ok in engines.values() if ok),
    })


# ── OCR Profiles ───────────────────────────────────────────────────────────────

@router.get("/profiles", response_model=SuccessResponse[list])
async def get_profiles(tenant: TenantContext = Depends(_require_admin)):
    return SuccessResponse(data=list_profiles())


@router.get("/profiles/{name}", response_model=SuccessResponse[dict])
async def get_profile_by_name(
    name:   str = Path(...),
    tenant: TenantContext = Depends(_require_admin),
):
    if name not in PROFILES:
        raise OCRServiceError(f"Profile '{name}' not found. Available: {list(PROFILES)}")
    return SuccessResponse(data=get_profile(name).to_dict())