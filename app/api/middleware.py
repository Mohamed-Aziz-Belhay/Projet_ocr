"""
app/api/middleware.py — Enterprise Edition
Request pipeline: Request ID → Rate Limit → Maintenance → Security Headers → Logging → CORS
"""
from __future__ import annotations
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

log = get_logger("http")
_EXEMPT = {"/health", "/", "/metrics", "/docs", "/redoc", "/openapi.json"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = str(uuid.uuid4())[:8]
        request.state.request_id = rid
        t0 = time.time()
        response = await call_next(request)
        ms = int((time.time() - t0) * 1000)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Processing-Time-Ms"] = str(ms)
        log.info("HTTP", extra={
            "request_id": rid, "method": request.method,
            "path": request.url.path, "status": response.status_code, "ms": ms,
            "ip": request.client.host if request.client else "unknown",
        })
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        from app.core.settings import get_settings
        s = get_settings()
        if not s.RATE_LIMIT_ENABLED or request.url.path in _EXEMPT:
            return await call_next(request)
        raw_key = request.headers.get(s.API_KEY_HEADER, "")
        if not raw_key:
            return await call_next(request)
        from app.api.rate_limiter import check_rate_limit
        allowed, rl_headers = check_rate_limit(raw_key[:12], s.RATE_LIMIT_DEFAULT_RPM, s.RATE_LIMIT_BURST)
        if not allowed:
            try:
                from app.core.metrics import rate_limit_hits_total
                rate_limit_hits_total.labels(org_slug="unknown").inc()
            except Exception:
                pass
            resp = JSONResponse(status_code=429, content={"error": "RATE_LIMIT_EXCEEDED", "detail": "Too many requests."})
            for k, v in rl_headers.items():
                resp.headers[k] = v
            return resp
        response = await call_next(request)
        for k, v in rl_headers.items():
            response.headers[k] = v
        return response


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in _EXEMPT or path.startswith("/admin") or path.startswith("/tenants"):
            return await call_next(request)
        try:
            from app.config.runtime import get_runtime_config
            cfg = get_runtime_config()
            if cfg.maintenance_mode:
                return JSONResponse(status_code=503, content={
                    "error": "MAINTENANCE", "detail": cfg.get("maintenance_message")
                })
        except Exception:
            pass
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(MaintenanceMiddleware)
    app.add_middleware(RequestLoggingMiddleware)