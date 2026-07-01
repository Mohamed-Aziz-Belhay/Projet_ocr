"""
app/routers/routes_monitoring.py

Proxy Prometheus -> backend -> Angular, reserve a l'administrateur.
Accepte un parametre `range` (fenetre PromQL) pour que le selecteur
de periode cote Angular soit reflete dans les requetes Prometheus.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.core.settings import get_settings
from app.core.rbac import require_admin
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])
settings = get_settings()

PROMETHEUS_URL = settings.PROMETHEUS_URL

VALID_RANGES = {"15m", "1h", "3h", "6h", "24h", "7d"}


async def get_current_admin_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Vous devez vous connecter.")
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré.") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")
    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    require_admin(user)
    return user


async def _pq(client: httpx.AsyncClient, promql: str) -> list[dict[str, Any]]:
    """Execute une requete PromQL, retourne [] si Prometheus est indisponible."""
    try:
        r = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=5.0,
        )
        r.raise_for_status()
        payload = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    if payload.get("status") != "success":
        return []
    return payload.get("data", {}).get("result", [])


def _scalar(result: list[dict[str, Any]], default: float = 0.0) -> float:
    if not result:
        return default
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return default


def _series(result: list[dict[str, Any]], label_key: str) -> list[dict[str, Any]]:
    out = []
    for item in result:
        lv = item.get("metric", {}).get(label_key, "unknown")
        try:
            v = float(item["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        out.append({"label": lv, "value": v})
    return out


@router.get("/metrics-summary")
async def get_metrics_summary(
    range: str = Query(default="6h", description="Fenetre PromQL : 15m,1h,3h,6h,24h,7d"),
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """
    Retourne un resume des metriques operationnelles pour la periode choisie.
    """
    if range not in VALID_RANGES:
        raise HTTPException(
            status_code=422,
            detail=f"Periode invalide '{range}'. Valeurs acceptees : {sorted(VALID_RANGES)}",
        )

    async with httpx.AsyncClient() as client:
        confidence = await _pq(
            client,
            "avg(ocr_extraction_confidence_sum / ocr_extraction_confidence_count)"
        )
        rate = await _pq(
            client,
            f"sum(rate(ocr_extractions_total[{range}]))"
        )
        cb = await _pq(client, "sum(ocr_circuit_breaker_state == 1)")
        jobs = await _pq(client, "ocr_job_queue_depth")
        duration = await _pq(
            client,
            f"histogram_quantile(0.95, sum(rate("
            f"ocr_processing_duration_seconds_bucket[{range}])) by (le, template_id))"
        )
        fields = await _pq(
            client,
            f"sum(rate(ocr_field_extraction_total[{range}])) by (field_name, outcome)"
        )

    return {
        "confidence_avg_pct":       round(_scalar(confidence) * 100, 1),
        "extraction_rate_per_sec":  round(_scalar(rate), 4),
        "circuit_breakers_open":    int(_scalar(cb)),
        "active_jobs":              int(_scalar(jobs)),
        "duration_p95_by_template": _series(duration, "template_id"),
        "field_outcomes":           _series(fields, "field_name"),
        "prometheus_available":     bool(confidence or rate or cb or jobs),
    }