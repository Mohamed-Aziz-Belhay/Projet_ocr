"""
app/schemas/jobs.py
"""
from __future__ import annotations
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime
from pydantic import BaseModel


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "done", "failed"]
    created_at: datetime
    updated_at: datetime
    result_url: Optional[str] = None
    error: Optional[str] = None
    progress_pct: int = 0


class JobListResponse(BaseModel):
    jobs: List[JobStatus]
    total: int
    page: int
    page_size: int