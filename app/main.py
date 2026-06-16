"""
app/main.py — Enterprise Edition + Static UI
"""
from __future__ import annotations

import os
import time
import traceback
from importlib import import_module
from typing import Iterable

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware import register_middleware
from app.core.errors import register_error_handlers
from app.core.logging import get_logger, setup_logging
from app.core.settings import get_settings

settings = get_settings()
log = get_logger("app.main")
_APP_START = time.time()


def install_openapi_security(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        security_schemes["ApiKeyAuth"] = {
            "type": "apiKey", "in": "header",
            "name": "X-API-Key", "description": "Clé API, dev: dev-key-123",
        }
        security_schemes["BearerAuth"] = {
            "type": "http", "scheme": "bearer",
            "bearerFormat": "JWT", "description": "JWT retourné par POST /auth/login",
        }
        schema["security"] = [{"ApiKeyAuth": []}]
        app.openapi_schema = schema
        return schema
    app.openapi = custom_openapi


def _register_core_engines() -> None:
    for mod in (
        "app.engines.paddle_engine",
        "app.engines.tesseract_engine",
        "app.engines.easyocr_engine",
        "app.engines.surya_engine",
    ):
        try:
            import_module(mod)
        except Exception as exc:
            log.warning("Engine registration skipped",
                        extra={"engine_module": mod, "error": str(exc)})


def _include_router_modules(
    app: FastAPI,
    modules: Iterable[str],
    critical_modules: Iterable[str] = (),
) -> None:
    critical = set(critical_modules or ())

    for module_name in modules:
        try:
            module = import_module(module_name)
            app.include_router(module.router)
            log.info("✅ Router enabled", extra={"router": module_name})

        except Exception as exc:
            tb = traceback.format_exc()
            log.error(
                f"❌ Router FAILED to load: {module_name}\n"
                f"   Error: {exc}\n"
                f"   Full traceback:\n{tb}"
            )
            print(f"\n{'='*60}")
            print(f"❌ ROUTER FAILED: {module_name}")
            print(f"   Error: {exc}")
            print(tb)
            print(f"{'='*60}\n")

            if module_name in critical:
                raise RuntimeError(
                    f"Critical router '{module_name}' failed: {exc}"
                ) from exc


def create_app() -> FastAPI:
    setup_logging(level=settings.LOG_LEVEL, use_json=settings.LOG_JSON)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "**OCR Microservice Enterprise** — multi-tenant, extraction générique, "
            "support arabe RTL, Swin Transformer.\n\n"
            "**Auth API:** header `X-API-Key`.\n"
            "**Auth utilisateur:** `POST /auth/login` puis `Authorization: Bearer <token>`.\n"
            "**Dev key:** `dev-key-123`\n"
            "**UI:** [Ouvrir l'interface](/)"
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # ── CORS ─────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:4200",
            "http://localhost",
            "http://localhost:80",
            "http://127.0.0.1:4200",
            "http://127.0.0.1",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_openapi_security(app)
    register_middleware(app)
    register_error_handlers(app)
    _register_core_engines()

    # ── Routers API ───────────────────────────────────────────
    _include_router_modules(
        app,
        (
            "app.routers.routes_health",
            "app.routers.routes_auth",
            "app.routers.routes_users",
            "app.routers.routes_dashboard",
            "app.routers.routes_assistant",
            "app.routers.routes_history",
            "app.routers.routes_extract",
            "app.routers.routes_templates",
            "app.routers.routes_admin",
            "app.routers.routes_benchmark",
            "app.routers.routes_swin",
            "app.routers.routes_scanner",
            "app.routers.routes_exports",
            "app.routers.routes_jobs",
            "app.routers.routes_tenants",
            "app.routers.routes_gdpr",
        ),
        critical_modules=(
            "app.routers.routes_health",
            "app.routers.routes_auth",
            "app.routers.routes_extract",
        ),
    )

    extract_routes = sorted(
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/extract")
    )
    if not extract_routes:
        raise RuntimeError("No /extract routes registered.")
    log.info("Extraction routes registered", extra={"routes": extract_routes})

    # ── Static files ──────────────────────────────────────────
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    if os.path.isdir(static_dir):
        def _static_file(filename: str) -> FileResponse:
            path = os.path.join(static_dir, filename)
            if not os.path.isfile(path):
                from fastapi import HTTPException
                raise HTTPException(404, f"Static file not found: {filename}")
            return FileResponse(path)

        @app.get("/", include_in_schema=False)
        async def serve_ui():
            return _static_file("index.html")

        @app.get("/templates-ui", include_in_schema=False)
        async def serve_templates_ui():
            return _static_file("templates.html")

        @app.get("/templates-ui/", include_in_schema=False)
        async def serve_templates_ui_slash():
            return _static_file("templates.html")

        @app.get("/templates.html", include_in_schema=False)
        async def serve_templates_html():
            return _static_file("templates.html")

        @app.get("/template_manager.html", include_in_schema=False)
        async def serve_template_manager_alias():
            return _static_file("templates.html")

        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        log.info("UI web disponible sur /")
        log.info("Template Manager disponible sur /templates-ui")
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return {"message": f"{settings.APP_NAME} v{settings.APP_VERSION} — /docs"}

    # ── Prometheus ────────────────────────────────────────────
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
        Instrumentator(
            should_group_status_codes=True,
            excluded_handlers=["/health", "/metrics", "/"],
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        log.info("Prometheus /metrics activé")
    except ImportError:
        log.warning("prometheus-fastapi-instrumentator non installé")

    # ── Startup ───────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup():
        try:
            from app.db.session import create_all_tables
            await create_all_tables()
            log.info("✅ Tables DB créées/vérifiées")
        except Exception as exc:
            log.warning("Init DB ignorée", extra={"error": str(exc)})

        try:
            from app.engines.engine_factory import available_engines
            engines = available_engines()
        except Exception:
            engines = {}

        try:
            from app.services.template_service import get_template_service
            template_count = len(get_template_service().list_all())
        except Exception:
            template_count = 0

        try:
            from app.services.scanner_watch_service import get_scanner_watch_service
            get_scanner_watch_service().start()
            log.info("Scanner watcher activé")
        except Exception:
            pass

        template_routes = [
            f"{m} {getattr(r, 'path', '')}"
            for r in app.routes
            if "template" in getattr(r, "path", "").lower()
            for m in getattr(r, "methods", ["?"])
        ]
        log.info(f"📋 Routes templates enregistrées : {template_routes}")

        log.info("Application démarrée", extra={
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "engines": [k for k, v in engines.items() if v],
            "templates_yaml": template_count,
        })

    # ── Shutdown ──────────────────────────────────────────────
    @app.on_event("shutdown")
    async def on_shutdown():
        try:
            from app.services.scanner_watch_service import get_scanner_watch_service
            await get_scanner_watch_service().stop()
        except Exception:
            pass
        try:
            from app.db.session import engine
            await engine.dispose()
        except Exception:
            pass
        log.info("Arrêt", extra={"uptime_s": round(time.time() - _APP_START, 1)})

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        workers=1 if settings.RELOAD else settings.WORKERS,
        log_level=settings.LOG_LEVEL.lower(),
    )