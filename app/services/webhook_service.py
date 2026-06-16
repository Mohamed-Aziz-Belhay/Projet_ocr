"""
app/services/webhook_service.py
Async webhook dispatcher with retry.
"""
from __future__ import annotations
import asyncio
from typing import Any, Dict

from app.core.settings import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
settings = get_settings()


async def send_webhook(url: str, payload: Dict[str, Any]) -> bool:
    """
    POST payload to url. Retries on failure.
    Returns True if delivered.
    """
    try:
        import httpx
    except ImportError:
        log.error("httpx not installed — webhooks disabled")
        return False

    for attempt in range(1, settings.WEBHOOK_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.WEBHOOK_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                log.info("Webhook delivered", extra={"url": url, "attempt": attempt})
                return True
        except Exception as exc:
            log.warning("Webhook failed", extra={"url": url, "attempt": attempt, "error": str(exc)})
            if attempt < settings.WEBHOOK_RETRIES:
                await asyncio.sleep(2 ** attempt)

    log.error("Webhook exhausted retries", extra={"url": url})
    return False
