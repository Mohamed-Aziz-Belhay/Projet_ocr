"""
app/db/models/job.py
Persistent job model in PostgreSQL.
"""
from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.organization import Organization


class Job(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "jobs"

    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization: Mapped["Organization"] = relationship("Organization", back_populates="jobs")

    api_key_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )

    status:       Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    error:        Mapped[Optional[str]] = mapped_column(Text)

    template_id:     Mapped[Optional[str]] = mapped_column(String(100))
    engine_used:     Mapped[Optional[str]] = mapped_column(String(50))
    file_name:       Mapped[Optional[str]] = mapped_column(String(500))
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    page_count:      Mapped[int]           = mapped_column(Integer, default=1)

    result_path:        Mapped[Optional[str]]   = mapped_column(Text)
    global_confidence:  Mapped[Optional[float]] = mapped_column(Float)
    field_count:        Mapped[int]             = mapped_column(Integer, default=0)
    processing_time_ms: Mapped[Optional[int]]   = mapped_column(Integer)

    celery_task_id: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    retry_count:    Mapped[int]           = mapped_column(Integer, default=0)

    webhook_url:          Mapped[Optional[str]]      = mapped_column(Text)
    webhook_delivered:    Mapped[bool]               = mapped_column(Boolean, default=False)
    webhook_delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_purged: Mapped[bool]               = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return f"<Job id={self.id[:8]} status={self.status}>"