"""
app/routers/routes_benchmark.py
Benchmark endpoints — admin scope required.

[SÉCURITÉ #4] Défense en profondeur (audit de sécurité complémentaire) :
Alignement sur routes_admin.py — double vérification rôle JWT réel +
scope dérivé, au lieu d'un scope de tenant seul (potentiellement obtenu
via une clé API partagée entre tous les rôles avant le fix). Ce router
n'est pas appelé par le frontend Angular (utilisé par le script de
campagne app.evaluation.benchmark_runner.py) ; ce durcissement est
appliqué par cohérence avec le reste des routes admin-scope, sans risque
de régression côté UI puisque rien ne l'appelle.
"""
from __future__ import annotations
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.benchmark_service import get_benchmark_service, BenchmarkService
from app.schemas.responses import SuccessResponse
from app.core.tenant import TenantContext, get_org_context_for_user
from app.core.logging import get_logger
from app.core.security import decode_access_token
from app.core.rbac import require_admin
from app.db.models.user import User
from app.db.session import get_db

log    = get_logger(__name__)
router = APIRouter(prefix="/benchmark", tags=["Benchmark (Phase D)"])


async def _get_current_user(
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

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Votre compte est en attente de validation par l'admin.")

    return user


async def _require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """
    [SÉCURITÉ #4] Double vérification, identique à routes_admin.py :
    rôle JWT réel + scope dérivé (get_org_context_for_user).
    """
    user = await _get_current_user(request, db)
    require_admin(user)

    tenant = await get_org_context_for_user(user, db)
    tenant.require_scope("admin")

    return tenant


@router.post("/run", response_model=SuccessResponse[dict],
             summary="Run benchmark from an uploaded JSON cases file")
async def run_benchmark_from_file(
    cases_file:  UploadFile = File(...),
    save_report: bool       = True,
    svc:         BenchmarkService = Depends(get_benchmark_service),
    tenant:      TenantContext = Depends(_require_admin),
):
    import tempfile, os
    content = await cases_file.read()
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        report = svc.run_from_file(tmp, save_report=save_report)
    finally:
        os.unlink(tmp)
    return SuccessResponse(data=report)


@router.post("/run-inline", response_model=SuccessResponse[dict],
             summary="Run benchmark from inline JSON cases")
async def run_benchmark_inline(
    cases:  List[Dict[str, Any]],
    svc:    BenchmarkService = Depends(get_benchmark_service),
    tenant: TenantContext = Depends(_require_admin),
):
    if not cases:
        raise HTTPException(422, "Cases list is empty")
    return SuccessResponse(data=svc.run_from_cases(cases))


@router.get("/reports", response_model=SuccessResponse[list])
async def list_reports(
    svc:    BenchmarkService = Depends(get_benchmark_service),
    tenant: TenantContext = Depends(_require_admin),
):
    return SuccessResponse(data=svc.list_reports())


@router.get("/reports/{filename}", response_model=SuccessResponse[dict])
async def get_report(
    filename: str,
    svc:      BenchmarkService = Depends(get_benchmark_service),
    tenant:   TenantContext = Depends(_require_admin),
):
    report = svc.get_report(filename)
    if not report:
        raise HTTPException(404, f"Report '{filename}' not found")
    return SuccessResponse(data=report)