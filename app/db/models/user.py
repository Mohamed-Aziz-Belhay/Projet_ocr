"""
app/db/models/user.py

User model for Angular/JWT authentication.

This model is independent from API-key authentication:
- Human users log in with email/password and receive a JWT.
- External systems can still use X-API-Key / TenantDep.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin, TimestampMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    organization_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # PBKDF2-SHA256 encoded by app.services.auth_service.
    # Never store raw passwords.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # Simple roles for PFE / enterprise demo.
    # Suggested values: admin, operator, viewer.
    role: Mapped[str] = mapped_column(String(50), default="operator", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    organization = relationship("Organization", lazy="joined")

    def has_role(self, *roles: str) -> bool:
        if self.is_superuser:
            return True
        return self.role in set(roles)

    def __repr__(self) -> str:
        return f"<User email={self.email} role={self.role}>"