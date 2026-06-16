"""
app/db/models/audit_log.py
Immutable audit trail — entries are NEVER updated or deleted (RGPD Art. 30).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, String, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.organization import Organization


class AuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    organization_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization", back_populates="audit_logs"
    )

    api_key_prefix: Mapped[Optional[str]] = mapped_column(String(20))
    ip_address:     Mapped[Optional[str]] = mapped_column(String(45))

    event_type:    Mapped[str]            = mapped_column(String(60), nullable=False, index=True)
    resource_type: Mapped[Optional[str]]  = mapped_column(String(60))
    resource_id:   Mapped[Optional[str]]  = mapped_column(String(100))
    http_method:   Mapped[Optional[str]]  = mapped_column(String(10))
    http_path:     Mapped[Optional[str]]  = mapped_column(String(500))
    http_status:   Mapped[Optional[int]]  = mapped_column()
    details:       Mapped[Optional[dict]] = mapped_column(JSON)
    is_anonymized: Mapped[bool]           = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<AuditLog {self.event_type} org={self.organization_id}>"


class AuditEvent:
    AUTH_SUCCESS     = "auth.success"
    AUTH_FAILURE     = "auth.failure"
    API_KEY_CREATED  = "api_key.created"
    API_KEY_REVOKED  = "api_key.revoked"
    EXTRACT_STARTED  = "extract.started"
    EXTRACT_DONE     = "extract.done"
    EXTRACT_FAILED   = "extract.failed"
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TEMPLATE_DELETED = "template.deleted"
    DATA_EXPORTED    = "gpdr.data_exported"
    DATA_ERASED      = "gpdr.data_erased"
    DATA_PURGED      = "gpdr.data_purged"
    ORG_CREATED      = "org.created"
    ORG_SUSPENDED    = "org.suspended"
    QUOTA_EXCEEDED   = "quota.exceeded"
    RATE_LIMIT_HIT   = "rate_limit.hit"
    CIRCUIT_OPEN     = "circuit_breaker.open"