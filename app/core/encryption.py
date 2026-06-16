"""
app/core/encryption.py
Field-level and file-level encryption using Fernet (AES-128-CBC + HMAC-SHA256).
Used for:
  - Encrypting stored files in S3/MinIO
  - Encrypting sensitive extracted fields at rest
  - Generating RGPD-compliant anonymized tokens

Key management:
  - ENCRYPTION_KEY env var → base64-encoded 32-byte Fernet key
  - Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations
import base64
import hashlib
from typing import Optional

from app.core.logging import get_logger

log = get_logger(__name__)

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet
    from app.core.settings import get_settings
    settings = get_settings()
    if not settings.ENCRYPTION_KEY:
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(settings.ENCRYPTION_KEY.encode())
        return _fernet
    except Exception as exc:
        log.error("Failed to initialize Fernet", extra={"error": str(exc)})
        return None


# ── File encryption ───────────────────────────────────────────────────────────

def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt raw bytes. Returns ciphertext or original if no key configured."""
    f = _get_fernet()
    if f is None:
        return data
    return f.encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    """Decrypt ciphertext. Returns original if no key configured."""
    f = _get_fernet()
    if f is None:
        return data
    try:
        return f.decrypt(data)
    except Exception as exc:
        log.error("Decryption failed", extra={"error": str(exc)})
        raise


# ── Field encryption ──────────────────────────────────────────────────────────

def encrypt_field(value: str) -> str:
    """Encrypt a string field. Returns base64 ciphertext."""
    f = _get_fernet()
    if f is None:
        return value
    return f.encrypt(value.encode()).decode()


def decrypt_field(value: str) -> str:
    """Decrypt an encrypted field."""
    f = _get_fernet()
    if f is None:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value   # graceful degradation


# ── RGPD anonymisation ────────────────────────────────────────────────────────

def anonymize_value(value: str, salt: str = "") -> str:
    """
    One-way anonymization for RGPD.
    Produces a consistent pseudonym (SHA-256 truncated).
    Used when replacing PII in audit logs after retention period.
    """
    digest = hashlib.sha256((salt + value).encode()).digest()
    return "anon_" + base64.urlsafe_b64encode(digest[:12]).decode()


def is_encryption_enabled() -> bool:
    from app.core.settings import get_settings
    return bool(get_settings().ENCRYPTION_KEY)
