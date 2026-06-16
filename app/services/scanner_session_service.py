from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


SESSION_DIR = Path("app/data/scanner_session")
SESSION_FILE = SESSION_DIR / "active_user.json"


def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def set_active_scanner_user(
    *,
    user_id: str,
    user_email: str | None,
    user_role: str | None,
    organization_id: str | None = None,
    full_name: str | None = None,
    ttl_minutes: int = 720,
) -> dict:
    """
    Enregistre l'utilisateur connecté comme propriétaire actuel
    des scans automatiques.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    now = _now()
    payload = {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "organization_id": organization_id,
        "full_name": full_name,
        "claimed_at": _iso(now),
        "expires_at": _iso(now + timedelta(minutes=ttl_minutes)),
    }

    SESSION_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return payload


def get_active_scanner_user() -> Optional[dict[str, Any]]:
    """
    Retourne l'utilisateur scanner actif.
    Si la session est expirée ou invalide, retourne None.
    """
    if not SESSION_FILE.exists():
        return None

    try:
        payload = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    expires_at = payload.get("expires_at")

    if not expires_at:
        return None

    try:
        exp = datetime.fromisoformat(expires_at)
    except Exception:
        return None

    if exp < _now():
        return None

    if not payload.get("user_id"):
        return None

    return payload


def clear_active_scanner_user() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()