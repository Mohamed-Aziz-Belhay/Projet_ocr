"""
app/core/tenant.py

Multi-tenant resolution.

This version is safe for local/dev execution:
- If ENABLE_DB_TENANT_LOOKUP=False, it skips PostgreSQL lookup completely.
- If DB lookup fails, it falls back to settings.ALLOWED_API_KEYS.
- Type hints avoid conditional SQLAlchemy classes to prevent Pylance errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Optional

from fastapi import Depends, HTTPException, Request, status

from app.core.logging import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)
settings = get_settings()

_DB_STACK_AVAILABLE = True
_DB_IMPORT_ERROR: Optional[str] = None

try:
    from sqlalchemy import select
    from app.db.models.api_key import ApiKey
    from app.db.models.organization import Organization
    from app.db.session import get_db
except Exception as exc:  # pragma: no cover
    _DB_STACK_AVAILABLE = False
    _DB_IMPORT_ERROR = str(exc)

    log.warning(
        "DB tenant stack unavailable; using settings-only auth",
        extra={"error": _DB_IMPORT_ERROR},
    )

    class Organization:  # type: ignore[no-redef]
        def __init__(
            self,
            id: str,
            name: str,
            slug: str,
            is_active: bool = True,
            is_suspended: bool = False,
            quota_pages_per_month: int = 10000,
            usage_pages_this_month: int = 0,
            quota_jobs_per_month: int = 1000,
            usage_jobs_this_month: int = 0,
        ) -> None:
            self.id = id
            self.name = name
            self.slug = slug
            self.is_active = is_active
            self.is_suspended = is_suspended
            self.quota_pages_per_month = quota_pages_per_month
            self.usage_pages_this_month = usage_pages_this_month
            self.quota_jobs_per_month = quota_jobs_per_month
            self.usage_jobs_this_month = usage_jobs_this_month

        @property
        def pages_remaining(self) -> int:
            return max(0, self.quota_pages_per_month - self.usage_pages_this_month)

        @property
        def jobs_remaining(self) -> int:
            return max(0, self.quota_jobs_per_month - self.usage_jobs_this_month)

    class ApiKey:  # type: ignore[no-redef]
        def __init__(
            self,
            id: str,
            name: str,
            key_hash: str,
            key_prefix: str,
            organization_id: str,
            scopes: str,
            is_active: bool = True,
        ) -> None:
            self.id = id
            self.name = name
            self.key_hash = key_hash
            self.key_prefix = key_prefix
            self.organization_id = organization_id
            self.scopes = scopes
            self.is_active = is_active
            self.last_used_at = None
            self.total_requests = 0

        def has_scope(self, scope: str) -> bool:
            return scope in self.scopes.split(",")

        @property
        def is_expired(self) -> bool:
            return False

        @staticmethod
        def hash(raw_key: str) -> str:
            import hashlib

            return hashlib.sha256(raw_key.encode()).hexdigest()

    async def get_db() -> None:
        return None


@dataclass
class TenantContext:
    """Injected into every authenticated request."""

    # Use Any here because Organization and ApiKey may come either from SQLAlchemy
    # models or from local fallback classes. This avoids Pylance invalid type-form errors.
    organization: Any
    api_key: Any
    raw_key_prefix: str

    @property
    def org_id(self) -> str:
        return str(self.organization.id)

    @property
    def org_slug(self) -> str:
        return str(self.organization.slug)

    def has_scope(self, scope: str) -> bool:
        return bool(self.api_key.has_scope(scope))

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key does not have required scope: '{scope}'",
            )

    def check_quota_pages(self, pages: int = 1) -> None:
        if self.organization.pages_remaining < pages:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Monthly page quota exceeded. "
                    f"Used: {self.organization.usage_pages_this_month}/"
                    f"{self.organization.quota_pages_per_month}"
                ),
            )

    def check_quota_jobs(self) -> None:
        if self.organization.jobs_remaining < 1:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Monthly job quota exceeded.",
            )


def _db_tenant_lookup_enabled() -> bool:
    """
    In local/PFE demo mode, disable PostgreSQL tenant lookup to avoid:
    DB tenant lookup failed, falling back.

    In production, set ENABLE_DB_TENANT_LOOKUP=True in .env.
    """
    return bool(getattr(settings, "ENABLE_DB_TENANT_LOOKUP", False))


def _make_synthetic_tenant(raw_key: str) -> TenantContext:
    synthetic_org = Organization(
        id="00000000-0000-0000-0000-000000000000",
        name="Default Org (settings)",
        slug="default",
        is_active=True,
        is_suspended=False,
        quota_pages_per_month=settings.DEFAULT_ORG_QUOTA_PAGES,
        usage_pages_this_month=0,
        quota_jobs_per_month=settings.DEFAULT_ORG_QUOTA_JOBS,
        usage_jobs_this_month=0,
    )

    synthetic_key = ApiKey(
        id="00000000-0000-0000-0000-000000000001",
        name="Settings Key",
        key_hash="",
        key_prefix=raw_key[:12],
        organization_id=synthetic_org.id,
        scopes="extract:read,extract:write,templates:read,templates:write,admin",
    )

    return TenantContext(
        organization=synthetic_org,
        api_key=synthetic_key,
        raw_key_prefix=raw_key[:12],
    )


async def _resolve_api_key(raw_key: str, db: Any) -> Optional[Any]:
    """
    Look up ApiKey by SHA-256 hash.

    Uses Any instead of AsyncSession/ApiKey type annotations because those
    classes are imported conditionally depending on the DB stack availability.
    """
    if not _DB_STACK_AVAILABLE or db is None:
        return None

    hashed = ApiKey.hash(raw_key)

    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.key_hash == hashed, ApiKey.is_active == True)  # noqa: E712
        .limit(1)
    )

    return result.scalar_one_or_none()


async def get_tenant_context(
    request: Request,
    db: Any = Depends(get_db),
) -> TenantContext:
    """
    FastAPI dependency.

    Resolution order:
    1. Read API key from header.
    2. If ENABLE_DB_TENANT_LOOKUP=True, try DB-backed tenant lookup.
    3. Fallback to settings.ALLOWED_API_KEYS for local/dev mode.
    """
    raw_key = request.headers.get(settings.API_KEY_HEADER, "")

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # ── Fast local/dev path: settings-only auth ────────────────────────────────
    if not _db_tenant_lookup_enabled():
        if settings.ALLOWED_API_KEYS and raw_key in settings.ALLOWED_API_KEYS:
            return _make_synthetic_tenant(raw_key)

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # ── DB-backed lookup ───────────────────────────────────────────────────────
    if _DB_STACK_AVAILABLE:
        try:
            api_key_obj = await _resolve_api_key(raw_key, db)

            if api_key_obj and not api_key_obj.is_expired:
                org = await db.get(Organization, api_key_obj.organization_id)

                if org and org.is_active and not org.is_suspended:
                    from datetime import datetime, timezone

                    api_key_obj.last_used_at = datetime.now(timezone.utc)
                    api_key_obj.total_requests += 1

                    return TenantContext(
                        organization=org,
                        api_key=api_key_obj,
                        raw_key_prefix=raw_key[:12],
                    )

        except Exception as exc:
            log.warning(
                "DB tenant lookup failed, falling back",
                extra={"error": str(exc)},
            )

    elif _DB_IMPORT_ERROR:
        log.info(
            "Tenant DB lookup skipped",
            extra={"reason": _DB_IMPORT_ERROR},
        )

    # ── Fallback: settings-based keys ──────────────────────────────────────────
    if settings.ALLOWED_API_KEYS and raw_key in settings.ALLOWED_API_KEYS:
        return _make_synthetic_tenant(raw_key)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]