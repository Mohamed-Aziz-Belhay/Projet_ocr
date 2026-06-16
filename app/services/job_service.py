"""
app/services/job_service.py
Job management service.
Uses the DB-backed implementation when SQLAlchemy/models are available and
falls back to an in-memory implementation otherwise.
"""
from __future__ import annotations
import uuid
from typing import TYPE_CHECKING, List, Optional, Tuple

from app.core.errors import JobNotFoundError
from app.core.logging import get_logger

log = get_logger(__name__)

# Real imports — only used at TYPE_CHECKING time so Pylance is happy;
# at runtime we guard with try/except below.
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.db.models.job import Job

_DB_JOB_STACK_AVAILABLE = True
_DB_JOB_IMPORT_ERROR: Optional[str] = None

try:
    from sqlalchemy import select, and_
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession  # noqa: F401
    from app.db.models.job import Job as _Job  # noqa: F401
except Exception as exc:
    _DB_JOB_STACK_AVAILABLE = False
    _DB_JOB_IMPORT_ERROR = str(exc)
    log.warning("Job DB stack unavailable", extra={"error": _DB_JOB_IMPORT_ERROR})


class JobService:
    """Tenant-isolated, PostgreSQL-backed job management when available."""

    def __init__(self, db: "AsyncSession") -> None:
        if not _DB_JOB_STACK_AVAILABLE:
            raise RuntimeError(_DB_JOB_IMPORT_ERROR or "DB job stack unavailable")
        self.db = db

    async def create(
        self,
        org_id: str,
        api_key_id: Optional[str] = None,
        template_id: Optional[str] = None,
        file_name: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
        page_count: int = 1,
        webhook_url: Optional[str] = None,
    ) -> "Job":
        from app.db.models.job import Job
        job = Job(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            api_key_id=api_key_id,
            template_id=template_id,
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            page_count=page_count,
            webhook_url=webhook_url,
            status="queued",
        )
        self.db.add(job)
        await self.db.flush()
        log.info("Job created", extra={"job_id": job.id, "org_id": org_id})
        return job

    async def get(self, job_id: str, org_id: str) -> "Job":
        from app.db.models.job import Job
        result = await self.db.execute(
            select(Job).where(and_(Job.id == job_id, Job.organization_id == org_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            raise JobNotFoundError(f"Job '{job_id}' not found")
        return job

    async def update(self, job_id: str, org_id: str, **kwargs) -> Optional["Job"]:
        from app.db.models.job import Job
        result = await self.db.execute(
            select(Job).where(and_(Job.id == job_id, Job.organization_id == org_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            return None
        allowed = {
            "status", "progress_pct", "error", "result_path", "celery_task_id",
            "global_confidence", "field_count", "processing_time_ms", "engine_used",
            "webhook_delivered", "webhook_delivered_at",
        }
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                setattr(job, k, v)
        await self.db.flush()
        return job

    async def list_jobs(
        self,
        org_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
    ) -> Tuple[List["Job"], int]:
        from app.db.models.job import Job
        conditions = [Job.organization_id == org_id, Job.is_purged == False]  # noqa: E712
        if status_filter:
            conditions.append(Job.status == status_filter)
        offset = (page - 1) * page_size
        result = await self.db.execute(
            select(Job).where(and_(*conditions))
            .order_by(Job.created_at.desc())
            .offset(offset).limit(page_size)
        )
        jobs = list(result.scalars().all())
        count_result = await self.db.execute(select(Job).where(and_(*conditions)))
        total = len(count_result.scalars().all())
        return jobs, total

    async def delete(self, job_id: str, org_id: str) -> bool:
        from app.db.models.job import Job
        result = await self.db.execute(
            select(Job).where(and_(Job.id == job_id, Job.organization_id == org_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            return False
        await self.db.delete(job)
        await self.db.flush()
        return True


# ── In-memory fallback ────────────────────────────────────────────────────────

class _InMemoryJobService:
    def __init__(self) -> None:
        self._jobs: dict = {}

    def create_sync(self, job_id: Optional[str] = None) -> str:
        from datetime import datetime, timezone
        jid = job_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self._jobs[jid] = {
            "job_id": jid, "status": "queued",
            "created_at": now, "updated_at": now,
            "result_url": None, "result_path": None,
            "error": None, "progress_pct": 0,
            "retry_count": 0, "webhook_delivered": False, "is_purged": False,
        }
        return jid

    def get_sync(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if not job:
            raise JobNotFoundError(f"Job '{job_id}' not found")
        return job

    def update_sync(self, job_id: str, **kwargs) -> None:
        from datetime import datetime, timezone
        job = self._jobs.get(job_id)
        if not job:
            return
        for k, v in kwargs.items():
            if v is not None:
                job[k] = v
        job["updated_at"] = datetime.now(timezone.utc)

    def list_sync(self) -> list:
        return list(self._jobs.values())


_mem_service: Optional[_InMemoryJobService] = None


def get_job_service() -> _InMemoryJobService:
    global _mem_service
    if _mem_service is None:
        _mem_service = _InMemoryJobService()
    return _mem_service