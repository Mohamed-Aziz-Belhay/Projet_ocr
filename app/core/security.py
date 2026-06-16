"""
app/core/security.py

API-key and JWT authentication helpers.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.settings import get_settings

settings = get_settings()

_api_key_scheme = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)
_bearer_scheme = HTTPBearer(auto_error=False)


# ── API Key ───────────────────────────────────────────────────────────────────

def verify_api_key(api_key: Optional[str] = Security(_api_key_scheme)) -> str:
    """
    FastAPI dependency — raises 401 if key is invalid or missing.

    Dev/test may be open only when ALLOWED_API_KEYS is empty.
    Production must never silently become anonymous.
    """
    env = (settings.ENVIRONMENT or "development").lower()

    if not settings.ALLOWED_API_KEYS:
        if env in {"development", "test"}:
            return "anonymous"

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key configuration missing",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key:
        for valid in settings.ALLOWED_API_KEYS:
            if secrets.compare_digest(api_key, valid):
                return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(
    subject: str,
    expires_in: Optional[int] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = int(time.time())
    exp = now + (expires_in or settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)

    payload: Dict[str, Any] = {
        "sub": str(subject),
        "exp": exp,
        "iat": now,
        "typ": "access",
    }

    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")

    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def get_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials