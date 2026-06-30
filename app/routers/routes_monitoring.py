"""
app/routers/routes_monitoring.py

Expose un resume des metriques Prometheus pour le frontend Angular.

Le frontend n'interroge JAMAIS Prometheus directement : ce router agit
comme proxy, ce qui evite (a) d'exposer l'URL/port de Prometheus au
navigateur du client, (b) les soucis CORS, (c) de coupler le frontend
au format de reponse natif de l'API Prometheus (verbeux, pense pour des
outils d'admin, pas pour une UI produit).

Reservee a l'administrateur (meme niveau d'acces que /dashboard).
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
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


async def get_current_admin_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Resout l'utilisateur courant depuis le token JWT puis verifie qu'il
    est administrateur, en reutilisant require_admin() de rbac.py.

    Pattern aligne sur _current_user_from_request() de routes_extract.py,
    avec en plus le controle de role admin specifique a ce router.
    """
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

    require_admin(user)  # leve 403 si le role n'est pas admin

    return user


# Requetes PromQL utilisees par le tableau de bord. Centralisees ici pour
# eviter de dupliquer des chaines de requete dans plusieurs endpoints.
QUERIES: dict[str, str] = {
    "confidence_avg": "avg(ocr_extraction_confidence_sum / ocr_extraction_confidence_count)",
    "extraction_rate": "sum(rate(ocr_extractions_total[5m]))",
    "circuit_breakers_open": "sum(ocr_circuit_breaker_state == 1)",
    "active_jobs": "ocr_job_queue_depth",
}

DURATION_QUERY = (
    "histogram_quantile(0.95, sum(rate("
    "ocr_processing_duration_seconds_bucket[5m])) by (le, template_id))"
)

FIELD_OUTCOMES_QUERY = "sum(rate(ocr_field_extraction_total[1h])) by (field_name, outcome)"


async def _prometheus_query(client: httpx.AsyncClient, promql: str) -> list[dict[str, Any]]:
    try:
        response = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        # Prometheus indisponible ou reponse mal formee : on ne fait pas
        # echouer toute la page monitoring pour une seule requete en panne.
        return []

    if payload.get("status") != "success":
        return []

    return payload.get("data", {}).get("result", [])


def _scalar_value(result: list[dict[str, Any]], default: float = 0.0) -> float:
    if not result:
        return default
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return default


def _labeled_series(result: list[dict[str, Any]], label_key: str) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for item in result:
        label_value = item.get("metric", {}).get(label_key, "unknown")
        try:
            value = float(item["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        series.append({"label": label_value, "value": value})
    return series


@router.get("/metrics-summary")
async def get_metrics_summary(
    current_user: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """Retourne un resume pret a afficher des metriques operationnelles."""
    async with httpx.AsyncClient() as client:
        confidence_result = await _prometheus_query(client, QUERIES["confidence_avg"])
        rate_result = await _prometheus_query(client, QUERIES["extraction_rate"])
        cb_result = await _prometheus_query(client, QUERIES["circuit_breakers_open"])
        jobs_result = await _prometheus_query(client, QUERIES["active_jobs"])
        duration_result = await _prometheus_query(client, DURATION_QUERY)
        fields_result = await _prometheus_query(client, FIELD_OUTCOMES_QUERY)

    return {
        "confidence_avg_pct": round(_scalar_value(confidence_result) * 100, 1),
        "extraction_rate_per_sec": round(_scalar_value(rate_result), 4),
        "circuit_breakers_open": int(_scalar_value(cb_result)),
        "active_jobs": int(_scalar_value(jobs_result)),
        "duration_p95_by_template": _labeled_series(duration_result, "template_id"),
        "field_outcomes": _labeled_series(fields_result, "field_name"),
        "prometheus_available": bool(
            confidence_result or rate_result or cb_result or jobs_result
        ),
    }