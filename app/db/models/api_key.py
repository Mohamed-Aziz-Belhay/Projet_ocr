"""
app/db/models/api_key.py
API keys scoped to an organisation.
Supports: rate_limit override, read-only mode, expiry, IP whitelist.
"""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.organization import Organization


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key(prefix: str = "ocr") -> tuple[str, str]:
    """Returns (raw_key, hashed_key). raw_key shown ONCE, only hash stored."""
    raw = f"{prefix}_live_" + secrets.token_urlsafe(36)
    return raw, _hash_key(raw)


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_keys"

    name:       Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash:   Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(20),  nullable=False)

    # Tenant
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization: Mapped["Organization"] = relationship("Organization", back_populates="api_keys")

    # Permissions
    scopes:       Mapped[str]  = mapped_column(Text, default="extract:read,extract:write,templates:read")
    is_active:    Mapped[bool] = mapped_column(Boolean, default=True)
    is_read_only: Mapped[bool] = mapped_column(Boolean, default=False)

    # Rate limit (0 = use org default)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=0)

    # Expiry & IP whitelist
    expires_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ip_whitelist: Mapped[Optional[str]]      = mapped_column(Text)   # JSON array as text

    # Usage tracking
    last_used_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_requests: Mapped[int]                = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<ApiKey prefix={self.key_prefix} org={self.organization_id}>"

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes.split(",")

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        from datetime import timezone
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def effective_rate_limit(self) -> int:
        if self.rate_limit_rpm > 0:
            return self.rate_limit_rpm
        from app.core.settings import get_settings
        return get_settings().RATE_LIMIT_DEFAULT_RPM

    @staticmethod
    def hash(raw_key: str) -> str:
        return _hash_key(raw_key)