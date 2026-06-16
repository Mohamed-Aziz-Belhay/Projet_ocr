"""
app/db/models/extraction_history.py
Stores OCR extraction history linked to authenticated Angular users.
"""
from __future__ import annotations

from typing import Optional
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Column, String

from app.db.base import Base, TimestampMixin, UUIDMixin


class ExtractionHistory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "extraction_history"

    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_email = Column(String(255), nullable=True, index=True)
    user_role = Column(String(50), nullable=True, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    document_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    engine_used: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    global_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    field_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user = relationship("User", lazy="joined")
