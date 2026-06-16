"""
app/schemas/responses.py
Generic envelope wrappers for all API responses.
"""
from __future__ import annotations
from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str
    path: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    engines: dict
    uptime_seconds: float
