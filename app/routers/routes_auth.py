"""
app/routers/routes_auth.py

JWT authentication endpoints for Angular users.
Includes:
- login/register/me
- forgot password by email verification code
- reset password with 30 minutes expiration
- change password for connected user
"""
from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
import hashlib
import os
import secrets
import smtplib

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, get_bearer_token
from app.core.settings import get_settings
from app.core.rbac import normalize_role
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import (
    CreateUserRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import (
    AuthService,
    hash_password as _project_get_password_hash,
    user_to_dict,
    verify_password as _project_verify_password,
)

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()

# In-memory reset store.
# ⚠️ Remis à zéro à chaque redémarrage du serveur.
# Pour la production, remplacez par une table DB.
RESET_CODES: dict[str, dict[str, Any]] = {}

RESET_CODE_EXPIRATION_MINUTES = 30
RESET_CODE_MAX_ATTEMPTS = 5
MAX_PASSWORD_LENGTH = 255


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def _normalize_public_register_role(value: str | None) -> str:
    role = normalize_role(value, default="simple_user")

    if role == "admin":
        return "simple_user"

    if role in {"simple_user", "operator", "viewer"}:
        return role

    return "simple_user"


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_reset_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def _validate_new_password(password: str) -> None:
    if not password:
        raise HTTPException(status_code=400, detail="Nouveau mot de passe obligatoire.")

    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Le nouveau mot de passe doit contenir au moins 8 caractères.",
        )

    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Le mot de passe est trop long. Maximum {MAX_PASSWORD_LENGTH} caractères.",
        )


def _get_smtp_config() -> dict[str, Any]:
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "from_email": (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip(),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"},
        "use_ssl": os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes", "on"},
    }


def _send_reset_email(email: str, code: str) -> str:
    cfg = _get_smtp_config()

    subject = "Code de réinitialisation du mot de passe - Arabsoft OCR"
    body = (
        "Bonjour,\n\n"
        f"Votre code de vérification est : {code}\n\n"
        f"Ce code expire dans {RESET_CODE_EXPIRATION_MINUTES} minutes.\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.\n\n"
        "Arabsoft OCR"
    )

    smtp_is_configured = bool(cfg["host"] and cfg["from_email"])

    if not smtp_is_configured:
        if settings.ENVIRONMENT in {"development", "test"}:
            print(f"[RESET PASSWORD DEV] Code pour {email} : {code}")
            print("[RESET PASSWORD DEV] SMTP non configuré, code affiché dans le terminal.")
            return "terminal"

        raise HTTPException(
            status_code=500,
            detail="SMTP non configuré. Impossible d'envoyer le code par email.",
        )

    message = EmailMessage()
    message["From"] = cfg["from_email"]
    message["To"] = email
    message["Subject"] = subject
    message.set_content(body)

    try:
        if cfg["use_ssl"]:
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=25) as server:
                if cfg["user"] and cfg["password"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=25) as server:
                if cfg["use_tls"]:
                    server.starttls()
                if cfg["user"] and cfg["password"]:
                    server.login(cfg["user"], cfg["password"])
                server.send_message(message)

        return "smtp"

    except Exception as exc:
        print(f"[RESET PASSWORD ERROR] SMTP failed for {email}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Erreur SMTP : impossible d'envoyer le code par email. {exc}",
        ) from exc


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    auth = AuthService(db)
    user = await auth.authenticate(email=payload.email, password=payload.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte est en attente de validation par l'admin.",
        )

    # Persist last_login_at updated in AuthService.authenticate().
    await db.commit()

    token, expires_in = auth.create_user_token(user)

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse(**user_to_dict(user)),
    )


@router.post("/register")
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if settings.ENVIRONMENT not in {"development", "test"}:
        raise HTTPException(
            status_code=403,
            detail="Inscription désactivée hors environnement development/test",
        )

    auth = AuthService(db)

    requested_role = getattr(payload, "requested_role", None) or getattr(payload, "role", None)
    role = _normalize_public_register_role(requested_role)
    is_pending_operator = role == "operator"

    try:
        user = await auth.create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            role=role,
            is_superuser=False,
            is_active=not is_pending_operator,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if is_pending_operator:
        return {
            "status": "pending",
            "message": "Votre demande de compte opérateur a été envoyée. Vous devez attendre la validation de l'admin.",
            "user": user_to_dict(user),
        }

    token, expires_in = auth.create_user_token(user)

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse(**user_to_dict(user)),
    )


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    email = _normalize_email(payload.email)

    if not email:
        raise HTTPException(status_code=400, detail="Email obligatoire.")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    generic_response = {
        "ok": True,
        "message": (
            "Si cet email existe, un code de vérification a été envoyé. "
            f"Le code expire dans {RESET_CODE_EXPIRATION_MINUTES} minutes."
        ),
        "expires_in_minutes": RESET_CODE_EXPIRATION_MINUTES,
    }

    # Security: do not reveal whether the account exists.
    if not user or not user.is_active:
        return generic_response

    code = _generate_reset_code()
    expires_at = datetime.utcnow() + timedelta(minutes=RESET_CODE_EXPIRATION_MINUTES)

    RESET_CODES[email] = {
        "code_hash": _hash_reset_code(code),
        "expires_at": expires_at,
        "attempts": 0,
    }

    _send_reset_email(email, code)

    return generic_response


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    email = _normalize_email(payload.email)
    code = (payload.code or "").strip()
    new_password = (payload.new_password or "").strip()

    if not email:
        raise HTTPException(status_code=400, detail="Email obligatoire.")

    if not code:
        raise HTTPException(status_code=400, detail="Code de vérification obligatoire.")

    _validate_new_password(new_password)

    reset_data = RESET_CODES.get(email)

    if not reset_data:
        raise HTTPException(
            status_code=400,
            detail="Code invalide ou expiré. Si le serveur a redémarré, veuillez demander un nouveau code.",
        )

    now = datetime.utcnow()

    if now > reset_data["expires_at"]:
        RESET_CODES.pop(email, None)
        raise HTTPException(
            status_code=400,
            detail="Le code de vérification a expiré. Veuillez demander un nouveau code.",
        )

    reset_data["attempts"] = int(reset_data.get("attempts", 0)) + 1

    if reset_data["attempts"] > RESET_CODE_MAX_ATTEMPTS:
        RESET_CODES.pop(email, None)
        raise HTTPException(
            status_code=400,
            detail="Nombre maximal de tentatives dépassé. Veuillez demander un nouveau code.",
        )

    code_hash_received = _hash_reset_code(code)
    code_hash_stored = reset_data["code_hash"]

    if code_hash_received != code_hash_stored:
        raise HTTPException(status_code=400, detail="Code de vérification invalide.")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        RESET_CODES.pop(email, None)
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")

    if not user.is_active:
        RESET_CODES.pop(email, None)
        raise HTTPException(status_code=403, detail="Compte inactif.")

    try:
        user.password_hash = _project_get_password_hash(new_password)
        await db.commit()

    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Impossible de modifier le mot de passe.",
        ) from exc

    RESET_CODES.pop(email, None)

    return {
        "ok": True,
        "message": "Mot de passe modifié avec succès. Vous pouvez maintenant vous connecter.",
    }


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    token: str = Depends(get_bearer_token),
    db: AsyncSession = Depends(get_db),
):
    token_payload = decode_access_token(token)
    user_id = token_payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Token invalide.")

    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable ou inactif.")

    current_password = (payload.current_password or "").strip()
    new_password = (payload.new_password or "").strip()

    if not _project_verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Ancien mot de passe incorrect.")

    _validate_new_password(new_password)

    if current_password == new_password:
        raise HTTPException(
            status_code=400,
            detail="Le nouveau mot de passe doit être différent de l'ancien.",
        )

    try:
        user.password_hash = _project_get_password_hash(new_password)
        await db.commit()

    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Impossible de modifier le mot de passe.",
        ) from exc

    return {
        "ok": True,
        "message": "Mot de passe modifié avec succès.",
    }


@router.get("/me", response_model=UserResponse)
async def me(token: str = Depends(get_bearer_token), db: AsyncSession = Depends(get_db)):
    payload = decode_access_token(token)
    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Votre compte est en attente de validation par l'admin.",
        )

    return UserResponse(**user_to_dict(user))


@router.post("/dev/seed-admin", response_model=UserResponse)
async def seed_admin(payload: CreateUserRequest, db: AsyncSession = Depends(get_db)):
    if settings.ENVIRONMENT not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="Disabled outside development/test")

    auth = AuthService(db)

    try:
        user = await auth.create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            role=payload.role or "admin",
            organization_id=payload.organization_id,
            is_superuser=payload.is_superuser,
            is_active=True,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return UserResponse(**user_to_dict(user))
