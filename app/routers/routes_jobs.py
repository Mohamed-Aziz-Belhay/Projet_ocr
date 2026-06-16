"""
app/routers/routes_jobs.py — Enterprise Edition
Full tenant-isolated job management. All operations scoped to org.
Supports a graceful in-memory fallback when the DB stack is unavailable.
"""
from __future__ import annotations
from fastapi import APIRouter, Path, Query, HTTPException
from app.core.tenant import TenantDep
from app.core.errors import JobNotFoundError
from app.core.logging import get_logger

router = APIRouter(prefix="/jobs", tags=["Jobs"])
log    = get_logger(__name__)


def _job_to_dict(job) -> dict:
    if isinstance(job, dict):
        return {
            "job_id":             job.get("job_id"),
            "status":             job.get("status"),
            "progress_pct":       job.get("progress_pct", 0),
            "template_id":        job.get("template_id"),
            "engine_used":        job.get("engine_used"),
            "file_name":          job.get("file_name"),
            "global_confidence":  job.get("global_confidence"),
            "field_count":        job.get("field_count"),
            "processing_time_ms": job.get("processing_time_ms"),
            "retry_count":        job.get("retry_count", 0),
            "webhook_delivered":  job.get("webhook_delivered", False),
            "is_purged":          job.get("is_purged", False),
            "result_url":         job.get("result_url"),
            "error":              job.get("error"),
            "created_at":         job.get("created_at").isoformat() if job.get("created_at") else None,
            "updated_at":         job.get("updated_at").isoformat() if job.get("updated_at") else None,
        }
    return {
        "job_id":             job.id,
        "status":             job.status,
        "progress_pct":       job.progress_pct,
        "template_id":        job.template_id,
        "engine_used":        job.engine_used,
        "file_name":          job.file_name,
        "global_confidence":  job.global_confidence,
        "field_count":        job.field_count,
        "processing_time_ms": job.processing_time_ms,
        "retry_count":        job.retry_count,
        "webhook_delivered":  job.webhook_delivered,
        "is_purged":          job.is_purged,
        "result_url":         f"/jobs/{job.id}/result" if job.status == "done" and not job.is_purged else None,
        "error":              job.error,
        "created_at":         job.created_at.isoformat(),
        "updated_at":         job.updated_at.isoformat() if getattr(job, "updated_at", None) else None,
    }


async def _list_jobs_backend(tenant: TenantDep, page: int, page_size: int, status_filter: str | None):
    try:
        from app.db.session import AsyncSessionLocal
        from app.services.job_service import JobService
        async with AsyncSessionLocal() as db:
            svc = JobService(db)
            return await svc.list_jobs(org_id=tenant.org_id, page=page, page_size=page_size, status_filter=status_filter)
    except Exception as exc:
        log.warning("Jobs DB backend unavailable; using in-memory fallback", extra={"error": str(exc)})
        from app.services.job_service import get_job_service
        jobs = get_job_service().list_sync()
        if status_filter:
            jobs = [j for j in jobs if j.get("status") == status_filter]
        total  = len(jobs)
        offset = (page - 1) * page_size
        return jobs[offset:offset + page_size], total


async def _get_job_backend(job_id: str, tenant: TenantDep):
    try:
        from app.db.session import AsyncSessionLocal
        from app.services.job_service import JobService
        async with AsyncSessionLocal() as db:
            return await JobService(db).get(job_id, tenant.org_id)
    except JobNotFoundError:
        raise
    except Exception as exc:
        log.warning("Job DB lookup unavailable; using in-memory fallback", extra={"error": str(exc), "job_id": job_id})
        from app.services.job_service import get_job_service
        return get_job_service().get_sync(job_id)


async def _delete_job_backend(job_id: str, tenant: TenantDep) -> bool:
    try:
        from app.db.session import AsyncSessionLocal
        from app.services.job_service import JobService
        async with AsyncSessionLocal() as db:
            deleted = await JobService(db).delete(job_id, tenant.org_id)
            await db.commit()
            return deleted
    except Exception as exc:
        log.warning("Job DB delete unavailable; using in-memory fallback", extra={"error": str(exc), "job_id": job_id})
        from app.services.job_service import get_job_service
        svc  = get_job_service()
        jobs = svc._jobs  # best-effort fallback for local dev/tests
        return jobs.pop(job_id, None) is not None


@router.get("", summary="List jobs for your organisation")
async def list_jobs(
    tenant:        TenantDep,
    page:          int         = Query(1, ge=1),
    page_size:     int         = Query(20, ge=1, le=100),
    status:        str | None  = Query(None),
):
    jobs, total = await _list_jobs_backend(tenant, page, page_size, status)
    return {
        "jobs":      [_job_to_dict(j) for j in jobs],
        "total":     total,
        "page":      page,
        "page_size": page_size,
    }


@router.get("/{job_id}", summary="Get job status")
async def get_job(
    tenant: TenantDep,
    job_id: str = Path(...),
):
    job = await _get_job_backend(job_id, tenant)
    return _job_to_dict(job)


@router.get("/{job_id}/result", summary="Get job result")
async def get_job_result(
    tenant: TenantDep,
    job_id: str = Path(...),
):
    from app.services.storage_service import get_storage_service
    job = await _get_job_backend(job_id, tenant)
    jd  = _job_to_dict(job)

    if jd["status"] != "done":
        raise HTTPException(425, f"Job status is '{jd['status']}' — result not ready")
    if jd["is_purged"]:
        raise HTTPException(410, "Result has been purged (RGPD)")
    if not jd.get("result_url") and not (isinstance(job, dict) and job.get("result_path")) and not getattr(job, "result_path", None):
        raise HTTPException(404, "Result file not found")

    result_path = job.get("result_path") if isinstance(job, dict) else job.result_path
    result = get_storage_service().load_result(result_path, org_id=tenant.org_id)
    if not result:
        raise HTTPException(404, "Could not load result from storage")
    return result


@router.delete("/{job_id}", summary="Delete a job")
async def delete_job(
    tenant: TenantDep,
    job_id: str = Path(...),
):
    deleted = await _delete_job_backend(job_id, tenant)
    return {"deleted": job_id, "success": deleted}