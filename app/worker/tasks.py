"""
app/worker/tasks.py
Celery tasks:
  - run_ocr_task         : async OCR extraction with retry
  - purge_expired_data   : RGPD TTL purge (scheduled)
  - reset_monthly_quotas : monthly quota reset (scheduled)
  - anonymize_old_audit_logs : RGPD log anonymisation (scheduled)
"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger

from app.worker.celery_app import celery_app

log = get_task_logger(__name__)


def _run_sync(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── OCR Extraction task ───────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.worker.tasks.run_ocr_task",
    max_retries=3,
    default_retry_delay=15,
    acks_late=True,
)
def run_ocr_task(
    self: Task,
    job_id: str,
    org_id: str,
    file_path: str,
    request_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute OCR extraction in a Celery worker.
    Updates job status in PostgreSQL at each step.
    """
    log.info(f"Starting OCR task job_id={job_id} org={org_id}")

    async def _execute():
        from app.db.session import AsyncSessionLocal
        from app.db.models.job import Job
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            # Fetch job
            result = await db.execute(select(Job).where(Job.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                log.error(f"Job {job_id} not found in DB")
                return {"error": "job_not_found"}

            try:
                # Update status: processing
                job.status = "processing"
                job.celery_task_id = self.request.id
                await db.commit()

                # Build request object
                from app.schemas.ocr import ExtractionRequest
                req = ExtractionRequest(**request_data)

                # Run pipeline
                from app.services.document_orchestrator import DocumentOrchestrator
                orchestrator = DocumentOrchestrator()
                extraction_result = orchestrator.process(
                    file_path=file_path,
                    request=req,
                    job_id=job_id,
                )

                # Persist result to S3/MinIO
                from app.services.storage_service import get_storage_service
                storage = get_storage_service()
                result_dict = extraction_result.model_dump(mode="json")
                result_path = await storage.save_result_async(org_id, job_id, result_dict)

                # Update job: done
                job.status = "done"
                job.progress_pct = 100
                job.result_path = result_path
                job.global_confidence = extraction_result.global_confidence
                job.field_count = len(extraction_result.fields)
                job.processing_time_ms = extraction_result.processing_time_ms
                job.engine_used = extraction_result.engine_used
                await db.commit()

                # Record metrics
                from app.core.metrics import record_extraction
                record_extraction(
                    org_slug=org_id[:8],
                    template_id=extraction_result.template_id,
                    engine=extraction_result.engine_used,
                    status=extraction_result.status,
                    confidence=extraction_result.global_confidence,
                    duration_seconds=extraction_result.processing_time_ms / 1000,
                    page_count=job.page_count,
                    fields=extraction_result.fields,
                )

                # Webhook
                if req.webhook_url:
                    from app.services.webhook_service import send_webhook
                    delivered = await send_webhook(str(req.webhook_url), result_dict)
                    job.webhook_delivered = delivered
                    job.webhook_delivered_at = datetime.now(timezone.utc)
                    await db.commit()

                # Cleanup upload
                storage.delete_upload(file_path)

                log.info(f"OCR task done job_id={job_id} status={job.status}")
                return result_dict

            except SoftTimeLimitExceeded:
                job.status = "failed"
                job.error = "Processing time limit exceeded"
                await db.commit()
                raise

            except Exception as exc:
                log.error(f"OCR task failed job_id={job_id}: {exc}")
                job.status = "failed"
                job.error = str(exc)
                job.retry_count += 1
                await db.commit()
                # Retry with exponential backoff
                raise self.retry(exc=exc, countdown=15 * (self.request.retries + 1))

    return _run_sync(_execute())


# ── RGPD: TTL purge ───────────────────────────────────────────────────────────

@celery_app.task(name="app.worker.tasks.purge_expired_data")
def purge_expired_data() -> Dict[str, int]:
    """
    Delete jobs + stored files past their organisation's data_retention_days.
    Runs every hour via beat schedule.
    """
    async def _purge():
        from app.db.session import AsyncSessionLocal
        from app.db.models.job import Job
        from app.db.models.organization import Organization
        from app.services.storage_service import get_storage_service
        from sqlalchemy import select, and_

        storage = get_storage_service()
        purged_count = 0
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            # Get all active orgs and their retention settings
            orgs = (await db.execute(select(Organization))).scalars().all()
            for org in orgs:
                cutoff = now - timedelta(days=org.data_retention_days)
                # Find stale jobs
                stale = (await db.execute(
                    select(Job).where(and_(
                        Job.organization_id == org.id,
                        Job.created_at < cutoff,
                        Job.is_purged == False,  # noqa: E712
                    ))
                )).scalars().all()

                for job in stale:
                    # Delete file from storage
                    if job.result_path:
                        try:
                            await storage.delete_object_async(job.result_path)
                        except Exception:
                            pass  # best effort
                    job.is_purged = True
                    job.purged_at = now
                    job.result_path = None
                    purged_count += 1

            await db.commit()

        log.info(f"RGPD purge: {purged_count} jobs purged")
        return {"purged": purged_count}

    return _run_sync(_purge())


# ── Monthly quota reset ───────────────────────────────────────────────────────

@celery_app.task(name="app.worker.tasks.reset_monthly_quotas")
def reset_monthly_quotas() -> Dict[str, int]:
    """Reset usage counters on the 1st of each month."""
    async def _reset():
        from app.db.session import AsyncSessionLocal
        from app.db.models.organization import Organization
        from sqlalchemy import select, update

        now = datetime.now(timezone.utc)
        if now.day != 1:
            return {"skipped": True}

        async with AsyncSessionLocal() as db:
            await db.execute(update(Organization).values(
                usage_pages_this_month=0,
                usage_jobs_this_month=0,
            ))
            await db.commit()

        log.info("Monthly quotas reset")
        return {"reset": True, "date": now.isoformat()}

    return _run_sync(_reset())


# ─ RGPD audit log anonymisation ──────────────────────────────────────────────

@celery_app.task(name="app.worker.tasks.anonymize_old_audit_logs")
def anonymize_old_audit_logs() -> Dict[str, int]:
    """Replace PII in old audit logs with pseudonymous hashes."""
    async def _anonymize():
        from app.db.session import AsyncSessionLocal
        from app.db.models.audit_log import AuditLog
        from app.core.encryption import anonymize_value
        from app.core.settings import get_settings
        from sqlalchemy import select, and_

        settings = get_settings()
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ANONYMIZE_LOGS_AFTER_DAYS)
        count = 0

        async with AsyncSessionLocal() as db:
            logs = (await db.execute(
                select(AuditLog).where(and_(
                    AuditLog.created_at < cutoff,
                    AuditLog.is_anonymized == False,  # noqa: E712
                ))
            )).scalars().all()

            for entry in logs:
                if entry.ip_address:
                    entry.ip_address = anonymize_value(entry.ip_address)
                if entry.api_key_prefix:
                    entry.api_key_prefix = "anon_****"
                entry.details = None
                entry.is_anonymized = True
                count += 1

            await db.commit()

        log.info(f"Anonymized {count} audit log entries")
        return {"anonymized": count}

    return _run_sync(_anonymize())