"""
app/services/audit_service.py
Centralized audit trail writer.
All security-relevant events pass through here.
Writes to PostgreSQL asynchronously when the DB stack is available.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

try:
    from app.db.models.audit_log import AuditLog, AuditEvent
    _AUDIT_DB_AVAILABLE = True
except Exception as exc:  # pragma: no cover - environment dependent
    _AUDIT_DB_AVAILABLE = False
    log.warning("Audit DB models unavailable; audit persistence disabled", extra={"error": str(exc)})

    class AuditEvent:
        AUTH_FAILURE = "auth.failure"
        EXTRACT_STARTED = "extract.started"
        EXTRACT_DONE = "extract.done"
        TEMPLATE_CREATED = "template.created"
        TEMPLATE_UPDATED = "template.updated"
        TEMPLATE_DELETED = "template.deleted"
        DATA_ERASED = "data.erased"
        QUOTA_EXCEEDED = "quota.exceeded"

    AuditLog = None  # type: ignore[assignment]


class AuditService:

    async def log(
        self,
        event_type: str,
        org_id: Optional[str] = None,
        api_key_prefix: Optional[str] = None,
        ip_address: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        http_method: Optional[str] = None,
        http_path: Optional[str] = None,
        http_status: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write an audit entry. Non-blocking — errors are logged but not raised."""
        if not _AUDIT_DB_AVAILABLE or AuditLog is None:
            log.info("Audit persistence skipped", extra={"event": event_type, "org_id": org_id})
            return
        try:
            from app.db.session import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                entry = AuditLog(
                    event_type=event_type,
                    organization_id=org_id,
                    api_key_prefix=api_key_prefix,
                    ip_address=ip_address,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    http_method=http_method,
                    http_path=http_path,
                    http_status=http_status,
                    details=details,
                )
                db.add(entry)
                await db.commit()
        except Exception as exc:
            log.error("Audit write failed", extra={"event": event_type, "error": str(exc)})

    def log_sync(self, event_type: str, **kwargs) -> None:
        """Fire-and-forget from sync context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.log(event_type, **kwargs))
            else:
                loop.run_until_complete(self.log(event_type, **kwargs))
        except Exception:
            pass

    async def auth_failure(self, ip: str, path: str, key_prefix: str = "") -> None:
        await self.log(
            AuditEvent.AUTH_FAILURE,
            api_key_prefix=key_prefix or "unknown",
            ip_address=ip,
            http_path=path,
        )

    async def extraction_started(
        self, org_id: str, job_id: str, template_id: Optional[str], api_key_prefix: str
    ) -> None:
        await self.log(
            AuditEvent.EXTRACT_STARTED,
            org_id=org_id,
            api_key_prefix=api_key_prefix,
            resource_type="job",
            resource_id=job_id,
            details={"template_id": template_id},
        )

    async def extraction_done(
        self, org_id: str, job_id: str, status: str, confidence: float
    ) -> None:
        await self.log(
            AuditEvent.EXTRACT_DONE,
            org_id=org_id,
            resource_type="job",
            resource_id=job_id,
            details={"status": status, "confidence": confidence},
        )

    async def template_event(
        self, action: str, org_id: str, template_id: str, api_key_prefix: str
    ) -> None:
        event_map = {
            "create": AuditEvent.TEMPLATE_CREATED,
            "update": AuditEvent.TEMPLATE_UPDATED,
            "delete": AuditEvent.TEMPLATE_DELETED,
        }
        await self.log(
            event_map.get(action, f"template.{action}"),
            org_id=org_id,
            api_key_prefix=api_key_prefix,
            resource_type="template",
            resource_id=template_id,
        )

    async def gdpr_erasure(self, org_id: str, subject_id: str, requester: str) -> None:
        await self.log(
            AuditEvent.DATA_ERASED,
            org_id=org_id,
            api_key_prefix=requester,
            resource_type="subject",
            resource_id=subject_id,
        )

    async def quota_exceeded(self, org_id: str, quota_type: str) -> None:
        await self.log(
            AuditEvent.QUOTA_EXCEEDED,
            org_id=org_id,
            details={"quota_type": quota_type},
        )


_audit_service: Optional[AuditService] = None


def get_audit_service() -> AuditService:
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service
