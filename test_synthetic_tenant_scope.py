# test_synthetic_tenant_scope.py
import os

os.environ["ENVIRONMENT"] = "production"

from app.core.settings import get_settings
get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

from app.core.tenant import _make_synthetic_tenant

tenant = _make_synthetic_tenant("fake-key-for-test")
print("Scopes accordés en production :", tenant.api_key.scopes)
print("A le scope admin :", tenant.has_scope("admin"), "(attendu: False)")