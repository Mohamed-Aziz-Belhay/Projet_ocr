"""
app/schemas/tenant.py
Pydantic request/response schemas for multi-tenant API.
Kept separate from ORM models to avoid leaking DB internals.
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


# ── Organisation ──────────────────────────────────────────────────────────────

class OrgCreateRequest(BaseModel):
    name: str                        = Field(..., min_length=2, max_length=200)
    contact_email: Optional[str]     = None
    quota_pages_per_month: int       = Field(10000, ge=0)
    quota_jobs_per_month: int        = Field(1000,  ge=0)
    data_retention_days: int         = Field(90,    ge=1, le=3650)


class OrgUpdateRequest(BaseModel):
    name: Optional[str]              = Field(None, min_length=2, max_length=200)
    contact_email: Optional[str]     = None
    quota_pages_per_month: Optional[int] = Field(None, ge=0)
    quota_jobs_per_month: Optional[int]  = Field(None, ge=0)
    data_retention_days: Optional[int]   = Field(None, ge=1, le=3650)
    is_active: Optional[bool]            = None


class OrgSummary(BaseModel):
    id: str
    slug: str
    name: str
    is_active: bool
    is_suspended: bool
    usage_pages: int
    quota_pages: int
    usage_jobs: int
    quota_jobs: int
    created_at: str


class OrgDetail(OrgSummary):
    contact_email: Optional[str]
    suspension_reason: Optional[str]
    data_retention_days: int
    gdpr_dpo_email: Optional[str]
    pages_remaining: int
    jobs_remaining: int


class OrgListResponse(BaseModel):
    organisations: List[OrgSummary]
    total: int
    page: int
    page_size: int


# ── API Key ───────────────────────────────────────────────────────────────────

class ApiKeyCreateRequest(BaseModel):
    name: str                              = Field(..., min_length=2, max_length=200)
    scopes: str                            = "extract:read,extract:write,templates:read"
    rate_limit_rpm: int                    = Field(0, ge=0)
    expires_at: Optional[datetime]         = None
    ip_whitelist: Optional[List[str]]      = None


class ApiKeyCreatedResponse(BaseModel):
    id: str
    name: str
    key: str            # shown once
    prefix: str
    scopes: str
    expires_at: Optional[str]
    warning: str        = "Save this key immediately — it will not be shown again."


class ApiKeySummary(BaseModel):
    id: str
    name: str
    prefix: str
    is_active: bool
    scopes: str
    rate_limit_rpm: int
    total_requests: int
    last_used_at: Optional[str]
    expires_at: Optional[str]


# ── Quota ─────────────────────────────────────────────────────────────────────

class QuotaInfo(BaseModel):
    used: int
    quota: int
    remaining: int
    pct: float


class UsageResponse(BaseModel):
    org_id: str
    pages: QuotaInfo
    jobs: QuotaInfo


# ── Job (enterprise view) ─────────────────────────────────────────────────────

class JobStatusEnterprise(BaseModel):
    job_id: str
    status: str
    progress_pct: int
    template_id: Optional[str]
    engine_used: Optional[str]
    file_name: Optional[str]
    global_confidence: Optional[float]
    field_count: int
    processing_time_ms: Optional[int]
    retry_count: int
    webhook_delivered: bool
    is_purged: bool
    result_url: Optional[str]
    error: Optional[str]
    created_at: str
    updated_at: str