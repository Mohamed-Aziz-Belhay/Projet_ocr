"""
app/routers/routes_admin.py
Admin endpoints — runtime config, engine status, OCR profiles.
Requires 'admin' scope on the API key.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, Body, Path

from app.config.runtime import get_runtime_config, RuntimeConfig
from app.config.ocr_profiles import list_profiles, get_profile, PROFILES
from app.engines.engine_factory import available_engines
from app.schemas.responses import SuccessResponse
from app.core.tenant import TenantDep
from app.core.errors import OCRServiceError

router = APIRouter(prefix="/admin", tags=["Admin"])


def _require_admin(tenant: TenantDep) -> TenantDep:
    """Shortcut: requires 'admin' scope on the API key."""
    tenant.require_scope("admin")
    return tenant


# ── Runtime config ─────────────────────────────────────────────────────────────

@router.get("/config", response_model=SuccessResponse[dict])
async def get_config(tenant: TenantDep):
    _require_admin(tenant)
    return SuccessResponse(data=get_runtime_config().get_all())


@router.patch("/config", response_model=SuccessResponse[dict])
async def update_config(
    tenant:  TenantDep,
    updates: Dict[str, Any] = Body(...),
):
    _require_admin(tenant)
    try:
        get_runtime_config().set_many(updates)
    except ValueError as exc:
        raise OCRServiceError(str(exc)) from exc
    return SuccessResponse(data=get_runtime_config().get_all())


@router.post("/config/reset", response_model=SuccessResponse[dict])
async def reset_config(tenant: TenantDep):
    _require_admin(tenant)
    get_runtime_config().reset()
    return SuccessResponse(data=get_runtime_config().get_all())


# ── Maintenance ────────────────────────────────────────────────────────────────

@router.post("/maintenance", response_model=SuccessResponse[dict])
async def toggle_maintenance(
    tenant:  TenantDep,
    enabled: bool = Body(..., embed=True),
    message: str  = Body("Service temporarily unavailable", embed=True),
):
    _require_admin(tenant)
    get_runtime_config().set_many({"maintenance_mode": enabled, "maintenance_message": message})
    return SuccessResponse(data={"maintenance_mode": enabled, "message": message})


# ── Engine status ──────────────────────────────────────────────────────────────

@router.get("/engines", response_model=SuccessResponse[dict])
async def engine_status(tenant: TenantDep):
    _require_admin(tenant)
    engines = available_engines()
    from app.engines.circuit_breaker import all_breaker_states
    return SuccessResponse(data={
        "engines":          engines,
        "circuit_breakers": all_breaker_states(),
        "available_count":  sum(1 for ok in engines.values() if ok),
    })


# ── OCR Profiles ───────────────────────────────────────────────────────────────

@router.get("/profiles", response_model=SuccessResponse[list])
async def get_profiles(tenant: TenantDep):
    _require_admin(tenant)
    return SuccessResponse(data=list_profiles())


@router.get("/profiles/{name}", response_model=SuccessResponse[dict])
async def get_profile_by_name(
    tenant: TenantDep,
    name:   str = Path(...),
):
    _require_admin(tenant)
    if name not in PROFILES:
        raise OCRServiceError(f"Profile '{name}' not found. Available: {list(PROFILES)}")
    return SuccessResponse(data=get_profile(name).to_dict())