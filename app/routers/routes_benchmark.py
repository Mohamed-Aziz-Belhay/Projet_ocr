"""
app/routers/routes_benchmark.py
Benchmark endpoints — admin scope required.
"""
from __future__ import annotations
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.services.benchmark_service import get_benchmark_service, BenchmarkService
from app.schemas.responses import SuccessResponse
from app.core.tenant import TenantDep
from app.core.logging import get_logger

log    = get_logger(__name__)
router = APIRouter(prefix="/benchmark", tags=["Benchmark (Phase D)"])


@router.post("/run", response_model=SuccessResponse[dict],
             summary="Run benchmark from an uploaded JSON cases file")
async def run_benchmark_from_file(
    tenant:      TenantDep,
    cases_file:  UploadFile = File(...),
    save_report: bool       = True,
    svc:         BenchmarkService = Depends(get_benchmark_service),
):
    tenant.require_scope("admin")
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
    tenant: TenantDep,
    cases:  List[Dict[str, Any]],
    svc:    BenchmarkService = Depends(get_benchmark_service),
):
    tenant.require_scope("admin")
    if not cases:
        raise HTTPException(422, "Cases list is empty")
    return SuccessResponse(data=svc.run_from_cases(cases))


@router.get("/reports", response_model=SuccessResponse[list])
async def list_reports(
    tenant: TenantDep,
    svc:    BenchmarkService = Depends(get_benchmark_service),
):
    tenant.require_scope("admin")
    return SuccessResponse(data=svc.list_reports())


@router.get("/reports/{filename}", response_model=SuccessResponse[dict])
async def get_report(
    tenant:   TenantDep,
    filename: str,
    svc:      BenchmarkService = Depends(get_benchmark_service),
):
    tenant.require_scope("admin")
    report = svc.get_report(filename)
    if not report:
        raise HTTPException(404, f"Report '{filename}' not found")
    return SuccessResponse(data=report)