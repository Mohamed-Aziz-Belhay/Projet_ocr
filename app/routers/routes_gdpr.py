"""
app/routers/routes_gdpr.py
RGPD compliance endpoints — Art. 17 (erasure), Art. 20 (portability), Art. 30 (audit).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models.job import Job
from app.db.models.audit_log import AuditLog, AuditEvent
from app.core.tenant import TenantDep
from app.services.audit_service import get_audit_service
from app.services.storage_service import get_storage_service
from app.schemas.responses import SuccessResponse
from app.core.logging import get_logger

log    = get_logger(__name__)
router = APIRouter(prefix="/gdpr", tags=["RGPD Compliance"])


# ── Data export (portability Art. 20) ─────────────────────────────────────────

@router.get(
    "/export",
    summary="Export all org data (RGPD Art. 20 — portability)",
)
async def export_data(
    tenant: TenantDep,
    include_results: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    org_id  = tenant.org_id
    storage = get_storage_service()

    result = await db.execute(
        select(Job)
        .where(and_(Job.organisation_id == org_id, Job.is_purged == False))  # noqa: E712
        .order_by(Job.created_at.desc())
    )

    jobs = result.scalars().all()
    export_list = []
    for job in jobs:
        entry = {
            "job_id":             job.id,
            "status":             job.status,
            "template_id":        job.template_id,
            "engine_used":        job.engine_used,
            "file_name":          job.file_name,
            "global_confidence":  job.global_confidence,
            "created_at":         job.created_at.isoformat(),
            "processing_time_ms": job.processing_time_ms,
        }
        if include_results and job.result_path and not job.is_purged:
            entry["result"] = storage.load_result(job.result_path, org_id=org_id)
        export_list.append(entry)

    payload = {
        "exported_at":    datetime.now(timezone.utc).isoformat(),
        "organisation":   {"id": org_id, "slug": tenant.org_slug, "name": tenant.organisation.name},
        "retention_days": tenant.organisation.data_retention_days,
        "total_jobs":     len(export_list),
        "jobs":           export_list,
    }
    await get_audit_service().log(
        AuditEvent.DATA_EXPORTED, org_id=org_id,
        api_key_prefix=tenant.raw_key_prefix,
        details={"job_count": len(export_list)},
    )
    json_bytes  = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    filename    = f"ocr_export_{tenant.org_slug}_{datetime.now().strftime('%Y%m%d')}.json"
    return StreamingResponse(
        iter([json_bytes]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Erase one job (Art. 17) ────────────────────────────────────────────────────

@router.delete(
    "/erase/{job_id}",
    response_model=SuccessResponse[dict],
    summary="Erase extracted data for one job (RGPD Art. 17)",
)
async def erase_job(
    tenant: TenantDep,
    job_id: str = Path(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Job).where(and_(Job.id == job_id, Job.organisation_id == tenant.org_id))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, f"Job '{job_id}' not found")
    if job.is_purged:
        return SuccessResponse(data={"job_id": job_id, "already_purged": True})

    storage = get_storage_service()
    if job.result_path:
        try:
            await storage.delete_object_async(job.result_path)
        except Exception as exc:
            log.warning("Could not delete result file", extra={"error": str(exc)})

    job.is_purged   = True
    job.purged_at   = datetime.now(timezone.utc)
    job.result_path = None
    await db.commit()

    await get_audit_service().log(
        AuditEvent.DATA_ERASED, org_id=tenant.org_id,
        api_key_prefix=tenant.raw_key_prefix,
        resource_type="job", resource_id=job_id,
    )
    return SuccessResponse(data={"job_id": job_id, "erased": True,
                                  "purged_at": job.purged_at.isoformat()})


# ── Erase all (Art. 17) ────────────────────────────────────────────────────────

@router.post(
    "/erase-all",
    response_model=SuccessResponse[dict],
    summary="Erase ALL extracted data for your organisation — irreversible",
)
async def erase_all(
    tenant: TenantDep,
    confirm: bool = Query(False, description="Must be true to proceed"),
    db: AsyncSession = Depends(get_db),
):
    if not confirm:
        raise HTTPException(400, "Set ?confirm=true to confirm this irreversible operation")

    storage = get_storage_service()
    result  = await db.execute(
        select(Job).where(and_(
            Job.organisation_id == tenant.org_id,
            Job.is_purged == False,  # noqa: E712
        ))
    )
    jobs  = result.scalars().all()
    count = 0
    now   = datetime.now(timezone.utc)
    for job in jobs:
        if job.result_path:
            try:
                await storage.delete_object_async(job.result_path)
            except Exception:
                pass
        job.is_purged   = True
        job.purged_at   = now
        job.result_path = None
        count += 1
    await db.commit()

    await get_audit_service().log(
        AuditEvent.DATA_ERASED, org_id=tenant.org_id,
        api_key_prefix=tenant.raw_key_prefix,
        details={"erased_count": count, "scope": "all"},
    )
    log.warning("RGPD erase-all", extra={"org_id": tenant.org_id, "count": count})
    return SuccessResponse(data={"erased_jobs": count, "organisation": tenant.org_slug})


# ── Retention info ─────────────────────────────────────────────────────────────

@router.get(
    "/retention-info",
    response_model=SuccessResponse[dict],
    summary="Data retention policy for your organisation",
)
async def retention_info(tenant: TenantDep):
    org = tenant.organisation
    return SuccessResponse(data={
        "organisation":      org.slug,
        "data_retention_days": org.data_retention_days,
        "anonymisation_days":  30,
        "dpo_email":           org.gdpr_dpo_email,
        "policy": (
            f"Data auto-purged after {org.data_retention_days} days. "
            "Audit logs anonymised after 30 days. "
            "Request erasure via DELETE /gdpr/erase-all."
        ),
    })


# ── Audit log (Art. 30) ────────────────────────────────────────────────────────

@router.get(
    "/audit-log",
    response_model=SuccessResponse[list],
    summary="Recent audit events for your organisation (RGPD Art. 30)",
)
async def get_audit_log(
    tenant: TenantDep,
    limit:       int             = Query(50, ge=1, le=200),
    event_type:  Optional[str]   = Query(None),
    db: AsyncSession = Depends(get_db),
):
    conditions = [AuditLog.organisation_id == tenant.org_id]
    if event_type:
        conditions.append(AuditLog.event_type == event_type)
    result = await db.execute(
        select(AuditLog)
        .where(and_(*conditions))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return SuccessResponse(data=[
        {
            "id":            e.id,
            "event_type":    e.event_type,
            "resource_type": e.resource_type,
            "resource_id":   e.resource_id,
            "http_path":     e.http_path,
            "created_at":    e.created_at.isoformat(),
            "is_anonymized": e.is_anonymized,
        }
        for e in logs
    ])