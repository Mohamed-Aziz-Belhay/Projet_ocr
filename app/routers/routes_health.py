"""
app/routers/routes_health.py
GET /health — liveness + readiness check
Inclut le statut Swin pour sauvegarder immédiatement si est actif.
"""
from __future__ import annotations
import time

from fastapi import APIRouter

from app.schemas.responses import HealthResponse
from app.engines.engine_factory import available_engines
from app.core.settings import get_settings

router    = APIRouter(tags=["Health"])
settings  = get_settings()
_START    = time.time()


@router.get("/health", summary="Health check + statut Swin")
def health_check():
    engines = available_engines()

    # Statut Swin
    try:
        from app.classifiers.swin_classifier import swin_status
        swin = swin_status()
    except Exception as exc:
        swin = {"active": False, "reason": str(exc)}

    return {
        "status":           "ok",
        "version":          settings.APP_VERSION,
        "environment":      settings.ENVIRONMENT,
        "uptime_seconds":   round(time.time() - _START, 1),
        "engines":          engines,
        "swin_classifier":  swin,
        "database":         settings.DATABASE_URL.split("://")[0],   # type seulelement
        "default_engine":   settings.DEFAULT_ENGINE,
    }


@router.get("/api", include_in_schema=False)
def api_root():
    return {"message": f"{settings.APP_NAME} v{settings.APP_VERSION} — /docs pour l'API"}