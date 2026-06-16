"""
app/services/tenant_service.py
CRUD operations for Organisations and ApiKeys.
Called by admin routes and the super-admin panel.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.organization import Organization
from app.db.models.api_key import ApiKey, generate_api_key
from app.core.logging import get_logger

log = get_logger(__name__)


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:100]


# ── Organisation CRUD ─────────────────────────────────────────────────────────

class TenantService:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Organisations ────────────────────────────────────────────────────────

    async def create_org(
        self,
        name: str,
        contact_email: Optional[str] = None,
        quota_pages: int = 10000,
        quota_jobs: int = 1000,
        retention_days: int = 90,
    ) -> Organization:
        slug = _slugify(name)
        # Ensure slug uniqueness
        existing = await self.get_org_by_slug(slug)
        if existing:
            slug = f"{slug}-{int(datetime.now().timestamp())}"

        org = Organization(
            name=name,
            slug=slug,
            contact_email=contact_email,
            quota_pages_per_month=quota_pages,
            quota_jobs_per_month=quota_jobs,
            data_retention_days=retention_days,
        )
        self.db.add(org)
        await self.db.flush()
        log.info("Organisation created", extra={"slug": slug, "id": org.id})
        return org

    async def get_org(self, org_id: str) -> Optional[Organization]:
        return await self.db.get(Organization, org_id)

    async def get_org_by_slug(self, slug: str) -> Optional[Organization]:
        result = await self.db.execute(
            select(Organization).where(Organization.slug == slug).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_orgs(self, page: int = 1, page_size: int = 20) -> Tuple[List[Organization], int]:
        offset = (page - 1) * page_size
        result = await self.db.execute(
            select(Organization).offset(offset).limit(page_size).order_by(Organization.created_at.desc())
        )
        orgs = result.scalars().all()
        count_result = await self.db.execute(select(Organization))
        total = len(count_result.scalars().all())
        return list(orgs), total

    async def update_org(self, org_id: str, **kwargs) -> Optional[Organization]:
        org = await self.get_org(org_id)
        if not org:
            return None
        allowed = {
            "name", "contact_email", "quota_pages_per_month",
            "quota_jobs_per_month", "data_retention_days", "is_active",
        }
        for key, val in kwargs.items():
            if key in allowed:
                setattr(org, key, val)
        await self.db.flush()
        return org

    async def suspend_org(self, org_id: str, reason: str) -> Optional[Organization]:
        org = await self.get_org(org_id)
        if not org:
            return None
        org.is_suspended = True
        org.suspension_reason = reason
        await self.db.flush()
        log.warning("Organisation suspended", extra={"org_id": org_id, "reason": reason})
        return org

    async def reinstate_org(self, org_id: str) -> Optional[Organization]:
        org = await self.get_org(org_id)
        if not org:
            return None
        org.is_suspended = False
        org.suspension_reason = None
        await self.db.flush()
        return org

    # ── ApiKeys ──────────────────────────────────────────────────────────────

    async def create_api_key(
        self,
        org_id: str,
        name: str,
        scopes: str = "extract:read,extract:write,templates:read",
        rate_limit_rpm: int = 0,
        expires_at: Optional[datetime] = None,
        ip_whitelist: Optional[List[str]] = None,
    ) -> Tuple[ApiKey, str]:
        """
        Returns (ApiKey ORM object, raw_key).
        raw_key is shown ONCE — it is not stored anywhere.
        """
        raw_key, hashed = generate_api_key(prefix="ocr")

        key_obj = ApiKey(
            name=name,
            key_hash=hashed,
            key_prefix=raw_key[:12],
            organization_id=org_id,
            scopes=scopes,
            rate_limit_rpm=rate_limit_rpm,
            expires_at=expires_at,
            ip_whitelist=",".join(ip_whitelist) if ip_whitelist else None,
        )
        self.db.add(key_obj)
        await self.db.flush()
        log.info("API key created", extra={"org_id": org_id, "prefix": raw_key[:12]})
        return key_obj, raw_key

    async def list_api_keys(self, org_id: str) -> List[ApiKey]:
        result = await self.db.execute(
            select(ApiKey)
            .where(ApiKey.organization_id == org_id)
            .order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke_api_key(self, key_id: str, org_id: str) -> bool:
        result = await self.db.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.organization_id == org_id)
        )
        key = result.scalar_one_or_none()
        if not key:
            return False
        key.is_active = False
        await self.db.flush()
        log.warning("API key revoked", extra={"key_id": key_id, "org_id": org_id})
        return True

    async def increment_usage(self, org_id: str, pages: int = 1) -> None:
        await self.db.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .values(
                usage_pages_this_month=Organization.usage_pages_this_month + pages,
                usage_jobs_this_month=Organization.usage_jobs_this_month + 1,
            )
        )