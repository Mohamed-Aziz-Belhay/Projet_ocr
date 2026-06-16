"""
app/routers/routes_users.py

Admin CRUD for Angular users.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.core.rbac import normalize_role
from app.db.models.user import User
from app.db.session import get_db
from app.services.auth_service import AuthService, user_to_dict

router = APIRouter(prefix="/users", tags=["Users"])


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    role: str = "operator"
    is_superuser: bool = False
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return authorization.split(" ", 1)[1].strip()


async def _current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_bearer_token(authorization)
    payload = decode_access_token(token)
    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Votre compte est en attente de validation par l'admin.")

    return user


def _require_admin(user: User) -> None:
    if not (getattr(user, "is_superuser", False) or getattr(user, "role", "") == "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")


def _normalize_admin_role(role: str | None, default: str = "operator") -> str:
    value = normalize_role(role, default=default)

    if value not in {"admin", "operator", "simple_user", "viewer"}:
        raise HTTPException(status_code=400, detail="Invalid role")

    return value


@router.get("")
async def list_users(current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return {"items": [user_to_dict(user) for user in result.scalars().all()]}


@router.get("/pending")
async def list_pending_operators(current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)
    result = await db.execute(
        select(User)
        .where(User.role == "operator", User.is_active == False)  # noqa: E712
        .order_by(User.created_at.desc())
    )
    return {"items": [user_to_dict(user) for user in result.scalars().all()]}


@router.get("/{user_id}")
async def get_user(user_id: str, current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user_to_dict(user)


@router.post("", status_code=201)
async def create_user(payload: UserCreateRequest, current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)
    role = _normalize_admin_role(payload.role, default="operator")
    auth = AuthService(db)

    try:
        user = await auth.create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            role=role,
            is_superuser=payload.is_superuser,
            is_active=payload.is_active,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return user_to_dict(user)


@router.patch("/{user_id}")
async def update_user(user_id: str, payload: UserUpdateRequest, current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name

    if payload.role is not None:
        user.role = _normalize_admin_role(payload.role)

    if payload.is_active is not None:
        user.is_active = payload.is_active

    if payload.is_superuser is not None:
        user.is_superuser = payload.is_superuser

    await db.commit()
    await db.refresh(user)
    return user_to_dict(user)


@router.post("/{user_id}/approve")
async def approve_user(user_id: str, current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = "operator"
    user.is_active = True

    await db.commit()
    await db.refresh(user)
    return user_to_dict(user)


@router.delete("/{user_id}")
async def disable_user(user_id: str, current: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    _require_admin(current)

    if str(user_id) == str(current.id):
        raise HTTPException(status_code=400, detail="You cannot disable your own account")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await db.commit()

    return {"ok": True, "id": user_id, "is_active": False}
