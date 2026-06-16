"""
app/services/auth_service.py

Authentication service:
- PBKDF2-SHA256 password hashing using only Python stdlib.
- User lookup with SQLAlchemy async.
- JWT access token creation through app.core.security.

No passlib dependency required.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.core.settings import get_settings
from app.db.models.user import User

settings = get_settings()

_PASSWORD_ALGORITHM = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 260_000


def hash_password(password: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """
    Return a Django-like encoded password string:
    pbkdf2_sha256$iterations$salt$hash

    The raw password is never stored.
    """
    if not password:
        raise ValueError("Password cannot be empty")

    salt = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")

    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )

    digest = base64.urlsafe_b64encode(dk).decode().rstrip("=")

    return f"{_PASSWORD_ALGORITHM}${iterations}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    if not password or not encoded:
        return False

    try:
        algorithm, iter_str, salt, expected = encoded.split("$", 3)

        if algorithm != _PASSWORD_ALGORITHM:
            return False

        iterations = int(iter_str)

        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )

        actual = base64.urlsafe_b64encode(dk).decode().rstrip("=")

        return hmac.compare_digest(actual, expected)

    except Exception:
        return False


def user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "is_superuser": bool(user.is_superuser),
        "organization_id": user.organization_id,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_by_email(self, email: str) -> Optional[User]:
        normalized = (email or "").strip().lower()

        if not normalized:
            return None

        result = await self.db.execute(
            select(User).where(User.email == normalized)
        )

        return result.scalar_one_or_none()

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        full_name: Optional[str] = None,
        role: str = "operator",
        organization_id: Optional[str] = None,
        is_superuser: bool = False,
        is_active: bool = True,
    ) -> User:
        normalized = email.strip().lower()

        existing = await self.get_user_by_email(normalized)

        if existing:
            raise ValueError(f"User already exists: {normalized}")

        user = User(
            email=normalized,
            full_name=full_name,
            password_hash=hash_password(password),
            role=role,
            organization_id=organization_id,
            is_superuser=is_superuser,
            is_active=is_active,
        )

        self.db.add(user)
        await self.db.flush()

        return user

    async def authenticate(self, *, email: str, password: str) -> Optional[User]:
        user = await self.get_user_by_email(email)

        if not user:
            return None

        if not user.is_active:
            return None

        if not verify_password(password, user.password_hash):
            return None

        user.last_login_at = datetime.now(timezone.utc)
        await self.db.flush()

        return user

    def create_user_token(self, user: User) -> tuple[str, int]:
        expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

        token = create_access_token(
            subject=user.id,
            expires_in=expires_in,
            extra_claims={
                "email": user.email,
                "role": user.role,
                "org_id": user.organization_id,
                "is_superuser": bool(user.is_superuser),
            },
        )

        return token, expires_in
