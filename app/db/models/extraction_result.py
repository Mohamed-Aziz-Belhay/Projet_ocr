"""
app/db/models/extraction_result.py

Stores full OCR result details separately from extraction_history.

Important:
This model intentionally does NOT use relationship(..., back_populates="result_detail")
because the existing ExtractionHistory model may not define the inverse property.

This avoids:
sqlalchemy.exc.InvalidRequestError:
Mapper[ExtractionHistory(extraction_history)] has no property 'result_detail'
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    history_id = Column(
        String(36),
        ForeignKey("extraction_history.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    job_id = Column(String(128), unique=True, index=True, nullable=True)

    raw_text = Column(Text, nullable=True)
    result_json = Column(JSONB, nullable=True)
    fields_json = Column(JSONB, nullable=True)
    diagnostics_json = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
