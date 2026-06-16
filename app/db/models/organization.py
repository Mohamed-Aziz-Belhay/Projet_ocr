"""
app/db/models/organization.py
Organisation = tenant. One org → many ApiKeys → many Jobs.
Quota tracking at org level.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.api_key import ApiKey
    from app.db.models.job import Job
    from app.db.models.audit_log import AuditLog


class Organization(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "organizations"

    name:  Mapped[str] = mapped_column(String(200), nullable=False)
    slug:  Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    contact_email: Mapped[Optional[str]] = mapped_column(String(254))
    description:   Mapped[Optional[str]] = mapped_column(Text)

    # Status
    is_active:         Mapped[bool]         = mapped_column(Boolean, default=True,  nullable=False)
    is_suspended:      Mapped[bool]         = mapped_column(Boolean, default=False, nullable=False)
    suspension_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Quotas (monthly rolling window)
    quota_pages_per_month: Mapped[int] = mapped_column(Integer, default=10000)
    quota_jobs_per_month:  Mapped[int] = mapped_column(Integer, default=1000)
    quota_templates:       Mapped[int] = mapped_column(Integer, default=50)

    # Usage counters (reset monthly by background task)
    usage_pages_this_month: Mapped[int] = mapped_column(Integer, default=0)
    usage_jobs_this_month:  Mapped[int] = mapped_column(Integer, default=0)

    # RGPD
    data_retention_days: Mapped[int]         = mapped_column(Integer, default=90)
    gdpr_dpo_email:      Mapped[Optional[str]] = mapped_column(String(254))

    # Relationships
    api_keys: Mapped[List["ApiKey"]] = relationship(
        "ApiKey", back_populates="organization", cascade="all, delete-orphan"
    )
    jobs: Mapped[List["Job"]] = relationship(
        "Job", back_populates="organization", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organisation slug={self.slug} active={self.is_active}>"

    @property
    def pages_remaining(self) -> int:
        return max(0, self.quota_pages_per_month - self.usage_pages_this_month)

    @property
    def jobs_remaining(self) -> int:
        return max(0, self.quota_jobs_per_month - self.usage_jobs_this_month)

    @property
    def org_slug(self) -> str:
        return self.slug

    @property
    def org_id(self) -> str:
        return self.id