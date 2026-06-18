"""
app/core/settings.py
Application settings.

This version adds switches for the specialized CIN pipeline,
passport/id templates and review workflow.
"""
from __future__ import annotations
from functools import lru_cache
from typing import List, Literal, Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "OCR Microservice Enterprise"
    APP_VERSION: str = "2.3.0"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"

    SECRET_KEY: Optional[str] = Field(default=None, min_length=32)
    API_KEY_HEADER: str = "X-API-Key"
    ALLOWED_API_KEYS: List[str] = []
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 240
    SUPER_ADMIN_KEY: Optional[str] = None

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 1
    RELOAD: bool = False

    DEFAULT_ENGINE: Literal["paddle", "tesseract", "easyocr", "surya"] = "paddle"
    FAST_MODE: bool = False
    ENABLE_PADDLE: bool = True
    ENABLE_TESSERACT: bool = True
    ENABLE_EASYOCR: bool = True
    ENABLE_SURYA_EXPERIMENTAL: bool = False
    ENABLE_TESSERACT_NUMERIC_FALLBACK: bool = False
    MAX_EASYOCR_ZONES_PER_JOB: int = 3
    MAX_SURYA_ZONES_PER_JOB: int = 2
    COMPARE_ALL_ENGINES_FOR_CIN: bool = False

    # CIN speed/accuracy profile.
    # fast: easyocr_boxes -> paddle_boxes -> targeted missing-field OCR.
    # balanced: fast path + limited full fallback only if needed.
    # full: exhaustive diagnostics/fallback mode.
    CIN_DEFAULT_MODE: Literal["fast", "balanced", "full"] = "balanced"
    CIN_FAST_ALLOW_MISSING_BIRTH_PLACE: bool = False
    CIN_BALANCED_ALLOW_FULL_FALLBACK: bool = True
    CIN_FULL_INCLUDE_TESSERACT: bool = False
    CIN_TARGETED_ENGINES: List[str] = ["easyocr"]
    CIN_BALANCED_TARGETED_ENGINES: List[str] = ["easyocr", "paddle"]

    # EasyOCR GPU/runtime controls.
    # EASYOCR_GPU=True means: use GPU when torch.cuda.is_available() is true, otherwise fallback to CPU.
    EASYOCR_GPU: bool = True
    EASYOCR_LANGS: List[str] = ["ar", "en"]
    EASYOCR_CANVAS_SIZE: int = 1280
    EASYOCR_MAG_RATIO: float = 1.0

    REVIEW_REQUIRED_THRESHOLD: float = 0.78
    CIN_PRIMARY_ENGINE: str = "paddle"
    CIN_SECONDARY_ENGINE: str = "easyocr"
    CIN_NUMERIC_ENGINE: str = "tesseract"
    CIN_EXPERIMENTAL_ENGINE: str = "surya"
    CIN_SUCCESS_MIN_CRITICAL_FIELDS: int = 3
    CIN_SUCCESS_MIN_BUSINESS_CONFIDENCE: float = 0.88
    CIN_REVIEW_IF_DATE_MISSING: bool = True
    CIN_REVIEW_IF_BIRTH_PLACE_MISSING: bool = False

    UPLOAD_DIR: str = "/tmp/ocr_uploads"
    RESULT_DIR: str = "/tmp/ocr_results"
    MAX_UPLOAD_MB: int = 20
    ALLOWED_EXTENSIONS: List[str] = ["pdf", "png", "jpg", "jpeg", "tiff", "webp"]

    TEMPLATES_DIR: str = "app/templates"

    # In local development/PFE demos, PostgreSQL is often not running.
    # Keep this False to avoid a DB connection attempt on every request.
    # Set ENABLE_DB_TENANT_LOOKUP=True in .env when you want real DB-backed tenants.
    ENABLE_DB_TENANT_LOOKUP: bool = False
    ENABLE_AUDIT_LOG: bool = False
    ENABLE_QUOTA_DB_UPDATE: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    CELERY_TASK_SOFT_TIME_LIMIT: int = 120
    CELERY_TASK_TIME_LIMIT: int = 180

    REDIS_URL: Optional[str] = None
    JOB_TTL_SECONDS: int = 3600
    MAX_RETRIES: int = 3

    S3_ENDPOINT_URL: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_BUCKET_UPLOADS: str = "ocr-uploads"
    S3_BUCKET_RESULTS: str = "ocr-results"
    S3_REGION: str = "us-east-1"
    S3_USE_SSL: bool = False

    ENCRYPTION_KEY: Optional[str] = None
    ENCRYPT_STORED_FILES: bool = True
    ENCRYPT_RESULT_FIELDS: bool = False

    DATA_RETENTION_DAYS: int = 90
    GDPR_DPO_EMAIL: Optional[str] = None
    ANONYMIZE_LOGS_AFTER_DAYS: int = 30

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT_RPM: int = 60
    RATE_LIMIT_BURST: int = 10

    CIRCUIT_BREAKER_THRESHOLD: int = 5
    CIRCUIT_BREAKER_TIMEOUT: int = 30

    DEFAULT_ORG_QUOTA_PAGES: int = 10000
    DEFAULT_ORG_QUOTA_JOBS: int = 1000

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    SWIN_MODEL_PATH: Optional[str] = None
    SWIN_CONFIDENCE_THRESHOLD: float = 0.75
    SWIN_MIN_ROUTE_CONFIDENCE: float = 0.70
    LAYOUT_MODEL_PATH: Optional[str] = None

    WEBHOOK_TIMEOUT_SECONDS: int = 10
    WEBHOOK_RETRIES: int = 3

    # ✅ APRÈS — bloquer en production, avertir en dev
    @model_validator(mode="after")
    def finalize_settings(self):
        import warnings
        env = (self.ENVIRONMENT or "development").lower()

        # SECRET_KEY
        if not self.SECRET_KEY:
            if env == "production":
                raise ValueError("SECRET_KEY is required in production")
            self.SECRET_KEY = "dev-secret-key-change-me-please-12345"
            warnings.warn(
                "SECRET_KEY not set — using insecure default. "
                "Set SECRET_KEY in .env for any non-dev environment.",
                stacklevel=2,
            )

        # ALLOWED_API_KEYS
        if not self.ALLOWED_API_KEYS:
            if env == "production":
                raise ValueError(
                    "ALLOWED_API_KEYS must be set in production. "
                    "Generate a key with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
            if env in {"development", "test"}:
                self.ALLOWED_API_KEYS = ["dev-key-123"]
                warnings.warn(
                    "ALLOWED_API_KEYS not set — using insecure 'dev-key-123'. "
                    "Set ALLOWED_API_KEYS in .env before deploying.",
                    stacklevel=2,
                )

        return self

    @field_validator("ALLOWED_API_KEYS", mode="before")
    @classmethod
    def parse_keys(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    model_config = {
    "env_file": ".env",
    "case_sensitive": True,
    "extra": "ignore",
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
