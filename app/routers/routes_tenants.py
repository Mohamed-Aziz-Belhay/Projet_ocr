"""
app/routers/routes_tenants.py
Super-admin routes for multi-tenant management.
Requires SUPER_ADMIN_KEY header (separate from regular API keys).
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Path, Query, Body
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.tenant_service import TenantService
from app.services.audit_service import get_audit_service, AuditEvent
from app.core.settings import get_settings
from app.schemas.responses import SuccessResponse
from app.core.logging import get_logger

log      = get_logger(__name__)
settings = get_settings()
router   = APIRouter(prefix="/tenants", tags=["Tenants (Super-Admin)"])


# ── Super-admin auth ──────────────────────────────────────────────────────────

def _require_super_admin(x_super_admin_key: str = Header(..., alias="X-Super-Admin-Key")):
    if not settings.SUPER_ADMIN_KEY:
        raise HTTPException(403, "Super-admin access not configured")
    import secrets
    if not secrets.compare_digest(x_super_admin_key, settings.SUPER_ADMIN_KEY):
        raise HTTPException(401, "Invalid super-admin key")
    return x_super_admin_key


# ── Request/Response schemas ──────────────────────────────────────────────────

class CreateOrgRequest(BaseModel):
    name:                  str
    contact_email:         Optional[str] = None
    quota_pages_per_month: int = 10000
    quota_jobs_per_month:  int = 1000
    data_retention_days:   int = 90


class UpdateOrgRequest(BaseModel):
    name:                  Optional[str] = None
    contact_email:         Optional[str] = None
    quota_pages_per_month: Optional[int] = None
    quota_jobs_per_month:  Optional[int] = None
    data_retention_days:   Optional[int] = None
    is_active:             Optional[bool] = None


class CreateApiKeyRequest(BaseModel):
    name:            str
    scopes:          str = "extract:read,extract:write,templates:read"
    rate_limit_rpm:  int = 0
    expires_at:      Optional[datetime] = None
    ip_whitelist:    Optional[List[str]] = None


class SuspendRequest(BaseModel):
    reason: str


# ── Organisation routes ───────────────────────────────────────────────────────

@router.post("", response_model=SuccessResponse[dict], status_code=201,
             summary="Create a new tenant organisation")
async def create_organisation(
    body: CreateOrgRequest,
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.create_org(
        name=body.name,
        contact_email=body.contact_email,
        quota_pages=body.quota_pages_per_month,
        quota_jobs=body.quota_jobs_per_month,
        retention_days=body.data_retention_days,
    )
    await db.commit()
    await get_audit_service().log(AuditEvent.ORG_CREATED, resource_id=org.id,
                                   details={"slug": org.slug})
    return SuccessResponse(data={"id": org.id, "slug": org.slug, "name": org.name,
                                  "created_at": org.created_at.isoformat()})


@router.get("", response_model=SuccessResponse[dict],
            summary="List all organisations")
async def list_organisations(
    page:      int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    orgs, total = await svc.list_orgs(page=page, page_size=page_size)
    return SuccessResponse(data={
        "organisations": [
            {"id": o.id, "slug": o.slug, "name": o.name,
             "is_active": o.is_active, "is_suspended": o.is_suspended,
             "usage_pages": o.usage_pages_this_month,
             "quota_pages": o.quota_pages_per_month,
             "created_at": o.created_at.isoformat()}
            for o in orgs
        ],
        "total": total, "page": page, "page_size": page_size,
    })


@router.get("/{org_id}", response_model=SuccessResponse[dict],
            summary="Get organisation details")
async def get_organisation(
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.get_org(org_id)
    if not org:
        raise HTTPException(404, f"Organisation '{org_id}' not found")
    return SuccessResponse(data={
        "id": org.id, "slug": org.slug, "name": org.name,
        "contact_email": org.contact_email,
        "is_active": org.is_active, "is_suspended": org.is_suspended,
        "suspension_reason": org.suspension_reason,
        "quota_pages_per_month": org.quota_pages_per_month,
        "quota_jobs_per_month": org.quota_jobs_per_month,
        "usage_pages_this_month": org.usage_pages_this_month,
        "usage_jobs_this_month": org.usage_jobs_this_month,
        "pages_remaining": org.pages_remaining,
        "jobs_remaining": org.jobs_remaining,
        "data_retention_days": org.data_retention_days,
        "created_at": org.created_at.isoformat(),
    })


@router.patch("/{org_id}", response_model=SuccessResponse[dict],
              summary="Update organisation")
async def update_organisation(
    body: UpdateOrgRequest,
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.update_org(org_id, **body.model_dump(exclude_none=True))
    if not org:
        raise HTTPException(404, f"Organisation '{org_id}' not found")
    await db.commit()
    return SuccessResponse(data={"id": org.id, "updated": True})


@router.post("/{org_id}/suspend", response_model=SuccessResponse[dict])
async def suspend_organisation(
    body: SuspendRequest,
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.suspend_org(org_id, body.reason)
    if not org:
        raise HTTPException(404)
    await db.commit()
    await get_audit_service().log(AuditEvent.ORG_SUSPENDED, org_id=org_id,
                                   details={"reason": body.reason})
    return SuccessResponse(data={"id": org_id, "suspended": True})


@router.post("/{org_id}/reinstate", response_model=SuccessResponse[dict])
async def reinstate_organisation(
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.reinstate_org(org_id)
    if not org:
        raise HTTPException(404)
    await db.commit()
    return SuccessResponse(data={"id": org_id, "reinstated": True})


# ── API Key routes ────────────────────────────────────────────────────────────

@router.post("/{org_id}/keys", response_model=SuccessResponse[dict], status_code=201,
             summary="Create an API key for an organisation")
async def create_api_key(
    body: CreateApiKeyRequest,
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.get_org(org_id)
    if not org:
        raise HTTPException(404)
    key_obj, raw_key = await svc.create_api_key(
        org_id=org_id,
        name=body.name,
        scopes=body.scopes,
        rate_limit_rpm=body.rate_limit_rpm,
        expires_at=body.expires_at,
        ip_whitelist=body.ip_whitelist,
    )
    await db.commit()
    await get_audit_service().log(AuditEvent.API_KEY_CREATED, org_id=org_id,
                                   resource_id=key_obj.id)
    return SuccessResponse(data={
        "id": key_obj.id,
        "name": key_obj.name,
        "key": raw_key,  # shown ONCE, never again
        "prefix": key_obj.key_prefix,
        "scopes": key_obj.scopes,
        "expires_at": key_obj.expires_at.isoformat() if key_obj.expires_at else None,
        "warning": "Save this key immediately. It will not be shown again.",
    })


@router.get("/{org_id}/keys", response_model=SuccessResponse[list],
            summary="List API keys")
async def list_api_keys(
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    keys = await svc.list_api_keys(org_id)
    return SuccessResponse(data=[
        {"id": k.id, "name": k.name, "prefix": k.key_prefix,
         "is_active": k.is_active, "scopes": k.scopes,
         "rate_limit_rpm": k.effective_rate_limit,
         "total_requests": k.total_requests,
         "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
         "expires_at": k.expires_at.isoformat() if k.expires_at else None}
        for k in keys
    ])


@router.delete("/{org_id}/keys/{key_id}", response_model=SuccessResponse[dict])
async def revoke_api_key(
    org_id: str = Path(...),
    key_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    ok = await svc.revoke_api_key(key_id, org_id)
    if not ok:
        raise HTTPException(404, "Key not found")
    await db.commit()
    await get_audit_service().log(AuditEvent.API_KEY_REVOKED, org_id=org_id, resource_id=key_id)
    return SuccessResponse(data={"revoked": key_id})


# ── Usage / quota ─────────────────────────────────────────────────────────────

@router.get("/{org_id}/usage", response_model=SuccessResponse[dict],
            summary="Quota usage")
async def get_usage(
    org_id: str = Path(...),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    org = await svc.get_org(org_id)
    if not org:
        raise HTTPException(404)
    return SuccessResponse(data={
        "org_id": org_id,
        "pages": {
            "used": org.usage_pages_this_month,
            "quota": org.quota_pages_per_month,
            "remaining": org.pages_remaining,
            "pct": round(org.usage_pages_this_month / max(org.quota_pages_per_month, 1) * 100, 1),
        },
        "jobs": {
            "used": org.usage_jobs_this_month,
            "quota": org.quota_jobs_per_month,
            "remaining": org.jobs_remaining,
            "pct": round(org.usage_jobs_this_month / max(org.quota_jobs_per_month, 1) * 100, 1),
        },
    })


# ── Org jobs (admin view) ─────────────────────────────────────────────────────

@router.get("/{org_id}/jobs", response_model=SuccessResponse[dict],
            summary="Org jobs (admin view)")
async def get_org_jobs(
    org_id: str = Path(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: str = Depends(_require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    jobs, total = await svc.list_jobs(org_id, page=page, page_size=page_size)
    return SuccessResponse(data={
        "jobs": [
            {"id": j.id, "status": j.status, "template_id": j.template_id,
             "file_name": j.file_name, "created_at": j.created_at.isoformat(),
             "processing_time_ms": j.processing_time_ms}
            for j in jobs
        ],
        "total": total, "page": page, "page_size": page_size,
    })