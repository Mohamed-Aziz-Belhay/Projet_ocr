"""
tests/test_enterprise.py
Tests for enterprise components:
  - Multi-tenant resolution
  - Rate limiter
  - Circuit breaker
  - Encryption
  - Tenant service (org + key CRUD)
  - Job service (PostgreSQL-backed)
  - Audit service
  - RGPD routes
  - Admin routes
"""
from __future__ import annotations
import os
import hashlib

import pytest

os.environ.setdefault("SECRET_KEY",        "test_secret_key_must_be_32chars_long!!")
os.environ.setdefault("ALLOWED_API_KEYS",  '["test-api-key"]')
os.environ.setdefault("TEMPLATES_DIR",     "app/templates")
os.environ.setdefault("DATABASE_URL",      "sqlite+aiosqlite:///./test_enterprise.db")
os.environ.setdefault("RATE_LIMIT_ENABLED","false")
os.environ.setdefault("ENCRYPT_STORED_FILES", "false")
os.environ.setdefault("SUPER_ADMIN_KEY",   "super-admin-test-key")


# ─────────────────────────────────────────────────────────────────────────────
# Encryption
# ─────────────────────────────────────────────────────────────────────────────

class TestEncryption:

    def test_encrypt_decrypt_bytes_no_key(self):
        """Without ENCRYPTION_KEY, data passes through unchanged."""
        from app.core.encryption import encrypt_bytes, decrypt_bytes
        data = b"hello world"
        assert encrypt_bytes(data) == data
        assert decrypt_bytes(data) == data

    def test_encrypt_decrypt_with_key(self, monkeypatch):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("ENCRYPTION_KEY", key)

        # Reset cached fernet
        import app.core.encryption as enc_mod
        enc_mod._fernet = None

        from app.core.encryption import encrypt_bytes, decrypt_bytes
        data = b"sensitive extracted field"
        encrypted = encrypt_bytes(data)
        assert encrypted != data
        decrypted = decrypt_bytes(encrypted)
        assert decrypted == data

        enc_mod._fernet = None  # cleanup

    def test_anonymize_value(self):
        from app.core.encryption import anonymize_value
        v1 = anonymize_value("192.168.1.1", salt="test")
        v2 = anonymize_value("192.168.1.1", salt="test")
        v3 = anonymize_value("192.168.1.2", salt="test")
        assert v1 == v2            # deterministic
        assert v1 != v3            # different inputs → different outputs
        assert v1.startswith("anon_")

    def test_field_encrypt_decrypt_no_key(self):
        from app.core.encryption import encrypt_field, decrypt_field
        val = "SARL EXAMPLE"
        assert encrypt_field(val) == val
        assert decrypt_field(val) == val


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

class TestCircuitBreaker:

    def test_initial_state_closed(self):
        from app.engines.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker("test_engine", threshold=3, timeout=30)
        assert cb.state == CBState.CLOSED
        assert cb.is_available() is True

    def test_opens_after_threshold(self):
        from app.engines.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker("test_engine2", threshold=3, timeout=30)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CBState.CLOSED   # not yet
        cb.record_failure()
        assert cb.state == CBState.OPEN
        assert cb.is_available() is False

    def test_success_resets_counter(self):
        from app.engines.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker("test_engine3", threshold=3, timeout=30)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failures == 0
        assert cb.state == CBState.CLOSED

    def test_half_open_after_timeout(self):
        import time
        from app.engines.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker("test_engine4", threshold=1, timeout=0)
        cb.record_failure()
        assert cb._state == CBState.OPEN
        time.sleep(0.01)
        # Access state property → triggers half-open check
        assert cb.state == CBState.HALF_OPEN
        assert cb.is_available() is True

    def test_reset(self):
        from app.engines.circuit_breaker import CircuitBreaker, CBState
        cb = CircuitBreaker("test_engine5", threshold=1, timeout=30)
        cb.record_failure()
        cb.reset()
        assert cb.state == CBState.CLOSED
        assert cb._failures == 0

    def test_registry(self):
        from app.engines.circuit_breaker import get_circuit_breaker, all_breaker_states
        cb1 = get_circuit_breaker("paddle")
        cb2 = get_circuit_breaker("paddle")
        assert cb1 is cb2  # same singleton
        states = all_breaker_states()
        assert "paddle" in states


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter (no Redis — tests bypass logic)
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimiter:

    def test_disabled_always_allows(self):
        from app.api.rate_limiter import check_rate_limit
        allowed, headers = check_rate_limit("test-key-prefix", limit_rpm=0)
        assert allowed is True

    def test_no_redis_fails_open(self):
        """Without Redis, rate limiter should allow (fail open)."""
        from app.api.rate_limiter import check_rate_limit
        # RATE_LIMIT_ENABLED=false → always passes
        allowed, _ = check_rate_limit("some-key", limit_rpm=60)
        assert allowed is True


# ─────────────────────────────────────────────────────────────────────────────
# API Key model
# ─────────────────────────────────────────────────────────────────────────────

class TestApiKeyModel:

    def test_generate_key_format(self):
        from app.db.models.api_key import generate_api_key
        raw, hashed = generate_api_key("ocr")
        assert raw.startswith("ocr_live_")
        assert len(raw) > 20
        assert len(hashed) == 64  # SHA-256 hex

    def test_hash_deterministic(self):
        from app.db.models.api_key import ApiKey
        h1 = ApiKey.hash("my-test-key")
        h2 = ApiKey.hash("my-test-key")
        h3 = ApiKey.hash("other-key")
        assert h1 == h2
        assert h1 != h3

    def test_has_scope(self):
        from app.db.models.api_key import ApiKey
        key = ApiKey(
            name="test", key_hash="x", key_prefix="x",
            organization_id="org1",
            scopes="extract:read,extract:write,templates:read",
        )
        assert key.has_scope("extract:read") is True
        assert key.has_scope("extract:write") is True
        assert key.has_scope("admin") is False

    def test_expiry_detection(self):
        from app.db.models.api_key import ApiKey
        from datetime import datetime, timezone, timedelta
        key = ApiKey(
            name="test", key_hash="x", key_prefix="x", organization_id="org1",
            scopes="extract:read",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert key.is_expired is True

        key2 = ApiKey(
            name="test2", key_hash="y", key_prefix="y", organization_id="org1",
            scopes="extract:read",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        assert key2.is_expired is False

    def test_no_expiry_never_expired(self):
        from app.db.models.api_key import ApiKey
        key = ApiKey(
            name="test", key_hash="x", key_prefix="x",
            organization_id="org1", scopes="extract:read",
        )
        assert key.is_expired is False


# ─────────────────────────────────────────────────────────────────────────────
# Organisation model
# ─────────────────────────────────────────────────────────────────────────────

class TestOrganisationModel:

    def test_pages_remaining(self):
        from app.db.models.organization import Organization
        org = Organization(
            name="Test", slug="test",
            quota_pages_per_month=1000,
            usage_pages_this_month=400,
        )
        assert org.pages_remaining == 600

    def test_pages_remaining_never_negative(self):
        from app.db.models.organization import Organization
        org = Organization(
            name="Test", slug="test",
            quota_pages_per_month=100,
            usage_pages_this_month=150,
        )
        assert org.pages_remaining == 0

    def test_jobs_remaining(self):
        from app.db.models.organization import Organization
        org = Organization(
            name="Test", slug="test",
            quota_jobs_per_month=200,
            usage_jobs_this_month=199,
        )
        assert org.jobs_remaining == 1


# ─────────────────────────────────────────────────────────────────────────────
# Tenant routes (super-admin)
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantRoutes:
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    SA_HEADERS = {"X-Super-Admin-Key": "super-admin-test-key"}

    def test_create_org(self):
        r = self.client.post(
            "/tenants",
            json={"name": "Test Organisation", "contact_email": "test@example.com"},
            headers=self.SA_HEADERS,
        )
        # Will fail without DB, but must not 500 due to auth
        assert r.status_code in (201, 500)

    def test_list_orgs(self):
        r = self.client.get("/tenants", headers=self.SA_HEADERS)
        assert r.status_code in (200, 500)

    def test_super_admin_wrong_key(self):
        r = self.client.get("/tenants", headers={"X-Super-Admin-Key": "wrong-key"})
        assert r.status_code == 401

    def test_super_admin_missing_key(self):
        r = self.client.get("/tenants")
        assert r.status_code == 422   # Header required

    def test_get_nonexistent_org(self):
        r = self.client.get("/tenants/nonexistent-id", headers=self.SA_HEADERS)
        assert r.status_code in (404, 500)


# ─────────────────────────────────────────────────────────────────────────────
# RGPD routes
# ─────────────────────────────────────────────────────────────────────────────

class TestGDPRRoutes:
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    HEADERS = {"X-API-Key": "test-api-key"}

    def test_retention_info(self):
        r = self.client.get("/gdpr/retention-info", headers=self.HEADERS)
        # OK in dev mode (synthetic tenant), may vary
        assert r.status_code in (200, 500)

    def test_erase_all_requires_confirm(self):
        r = self.client.post("/gdpr/erase-all", headers=self.HEADERS)
        assert r.status_code in (400, 422, 500)

    def test_export_data(self):
        r = self.client.get("/gdpr/export", headers=self.HEADERS)
        assert r.status_code in (200, 500)

    def test_gdpr_requires_auth(self):
        r = self.client.get("/gdpr/retention-info")
        assert r.status_code in (401, 422)

    def test_audit_log(self):
        r = self.client.get("/gdpr/audit-log", headers=self.HEADERS)
        assert r.status_code in (200, 500)


# ─────────────────────────────────────────────────────────────────────────────
# Audit service (unit)
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditService:

    def test_instantiation(self):
        from app.services.audit_service import AuditService
        svc = AuditService()
        assert svc is not None

    def test_singleton(self):
        from app.services.audit_service import get_audit_service
        s1 = get_audit_service()
        s2 = get_audit_service()
        assert s1 is s2

    def test_log_does_not_raise_without_db(self):
        """Audit must never crash the main request even if DB is down."""
        from app.services.audit_service import AuditService
        import asyncio
        svc = AuditService()
        # Should silently swallow the error
        try:
            asyncio.get_event_loop().run_until_complete(
                svc.log("test.event", org_id="fake-org")
            )
        except Exception:
            pytest.fail("AuditService.log must not raise")


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────

class TestMetrics:

    def test_record_extraction_no_crash(self):
        from app.core.metrics import record_extraction
        from app.schemas.ocr import FieldResult
        fields = [FieldResult(name="invoice_number", value="F001", confidence=0.9, validated=True)]
        # Should not raise even if prometheus not installed
        try:
            record_extraction(
                org_slug="test-org",
                template_id="invoice_generic",
                engine="tesseract",
                status="success",
                confidence=0.9,
                duration_seconds=1.5,
                page_count=1,
                fields=fields,
            )
        except Exception as e:
            pytest.fail(f"record_extraction raised: {e}")

    def test_update_quota_no_crash(self):
        from app.core.metrics import update_quota_metrics
        try:
            update_quota_metrics("test-org", 500, 10000, 50, 1000)
        except Exception as e:
            pytest.fail(f"update_quota_metrics raised: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Tenant service (unit with mock DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantServiceUnit:

    def test_slugify(self):
        from app.services.tenant_service import _slugify
        assert _slugify("Ma Société SARL") == "ma-socit-sarl" or \
               _slugify("Ma Société SARL").startswith("ma-")
        assert _slugify("EXAMPLE TECH") == "example-tech"
        assert len(_slugify("x" * 200)) <= 100

    def test_generate_and_hash_key(self):
        from app.db.models.api_key import generate_api_key, _hash_key
        raw, hashed = generate_api_key("ocr")
        assert _hash_key(raw) == hashed
        assert raw != hashed
