"""
app/services/AUTH_SERVICE_PATCH.py
Patch to integrate into app/services/auth_service.py

Adds: reject login if user.is_active = False
"""
from __future__ import annotations
from fastapi import HTTPException, status


def check_user_active(user: object) -> None:
    """
    Call this after password verification in authenticate().
    Raises 403 if user account is not active.
    """
    if not getattr(user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte est en attente de validation par l'admin.",
        )

# 2) Si ta methode create_user accepte deja is_active, garde-la.
# Sinon modifie sa signature pour accepter:
#
# async def create_user(
#     self,
#     email: str,
#     password: str,
#     full_name: str | None = None,
#     role: str = "viewer",
#     is_superuser: bool = False,
#     is_active: bool = True,
# ):
#
# Et dans la creation du User:
#
# user = User(
#     email=email,
#     hashed_password=hashed_password,
#     full_name=full_name,
#     role=role,
#     is_superuser=is_superuser,
#     is_active=is_active,
# )