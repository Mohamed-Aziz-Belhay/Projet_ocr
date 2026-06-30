"""
app/core/rbac.py

RBAC helpers for OCR platform.
Roles:
- admin: administration complete
- operator: extraction avancee + historique + assistant
- simple_user: extraction simple
"""
from __future__ import annotations

from typing import Iterable
from fastapi import HTTPException, status

ADMIN = "admin"
OPERATOR = "operator"
SIMPLE_USER = "simple_user"

ALL_ROLES = {ADMIN, OPERATOR, SIMPLE_USER}
EXTRACT_ROLES = {ADMIN, OPERATOR, SIMPLE_USER}
ADVANCED_USER_ROLES = {ADMIN, OPERATOR}
ADMIN_ROLES = {ADMIN}


def require_active_user(user) -> None:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Vous devez vous connecter.",
        )

    if not getattr(user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte est en attente de validation par l'admin.",
        )


def require_roles(user, roles: Iterable[str], action: str) -> None:
    require_active_user(user)

    if getattr(user, "is_superuser", False):
        return

    role = str(getattr(user, "role", "") or "").lower()

    if role not in set(roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acces refuse. Votre role ne permet pas {action}.",
        )


def require_admin(user) -> None:
    require_roles(user, ADMIN_ROLES, "d'acceder a l'administration")


def require_extract_permission(user) -> None:
    require_roles(user, EXTRACT_ROLES, "de lancer une extraction")


def require_advanced_user(user) -> None:
    require_roles(user, ADVANCED_USER_ROLES, "d'acceder a cette fonctionnalite avancee")


def normalize_role(role: str | None, default: str = SIMPLE_USER) -> str:
    value = str(role or default).strip().lower()

    if value in {"simple", "simple_user", "user"}:
        return SIMPLE_USER

    if value in ALL_ROLES:
        return value

    return default