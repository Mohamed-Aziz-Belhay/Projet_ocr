"""
EXEMPLE / PATCH pour app/routers/routes_auth.py

Adapte les noms de tes schemas si necessaire.

Logique:
- register(simple_user) => viewer actif
- register(operator) => operator inactif, en attente validation admin
- login refuse les comptes inactifs
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.auth_service import AuthService
from app.core.rbac import normalize_requested_role, is_operator_request

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class RegisterPayload(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    role: Optional[str] = "viewer"
    requested_role: Optional[str] = None
    requires_admin_approval: Optional[bool] = False


def _user_to_dict(user) -> dict:
    return {
        "id": str(getattr(user, "id")),
        "email": getattr(user, "email"),
        "full_name": getattr(user, "full_name", None),
        "role": getattr(user, "role", None),
        "is_active": bool(getattr(user, "is_active", False)),
        "is_superuser": bool(getattr(user, "is_superuser", False)),
        "organization_id": (
            str(getattr(user, "organization_id"))
            if getattr(user, "organization_id", None) is not None
            else None
        ),
    }


@router.post("/login")
async def login(payload: LoginPayload, db: AsyncSession = Depends(get_db)):
    auth = AuthService(db)
    user = await auth.authenticate(payload.email, payload.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
        )

    if not getattr(user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte est en attente de validation par l'admin.",
        )

    if hasattr(auth, "create_access_token"):
        access_token = auth.create_access_token(user)
    elif hasattr(auth, "create_token"):
        access_token = auth.create_token(user)
    else:
        from app.core.security import create_access_token
        access_token = create_access_token(user)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "user": _user_to_dict(user),
    }


@router.post("/register", status_code=201)
async def register(payload: RegisterPayload, db: AsyncSession = Depends(get_db)):
    auth = AuthService(db)

    existing = await auth.get_user_by_email(str(payload.email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un compte existe deja avec cet email.",
        )

    requested = payload.requested_role or payload.role
    role = normalize_requested_role(requested)

    pending_operator = is_operator_request(role)
    is_active = not pending_operator

    user = await auth.create_user(
        email=str(payload.email),
        password=payload.password,
        full_name=payload.full_name,
        role=role,
        is_superuser=False,
        is_active=is_active,
    )
    await db.commit()

    if pending_operator:
        return {
            "status": "pending",
            "message": "Votre demande de compte operateur a ete envoyee. Vous devez attendre la validation de l'admin.",
            "user": _user_to_dict(user),
        }

    if hasattr(auth, "create_access_token"):
        access_token = auth.create_access_token(user)
    elif hasattr(auth, "create_token"):
        access_token = auth.create_token(user)
    else:
        from app.core.security import create_access_token
        access_token = create_access_token(user)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "user": _user_to_dict(user),
    }
