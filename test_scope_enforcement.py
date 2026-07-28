# test_scope_enforcement.py
from fastapi.testclient import TestClient
from app.main import app
from app.core.tenant import get_tenant_context, TenantContext, Organization, ApiKey

def make_limited_tenant():
    org = Organization(id="test-org", name="Test Org", slug="test")
    key = ApiKey(
        id="test-key", name="Limited", key_hash="", key_prefix="test",
        organization_id=org.id,
        scopes="extract:read,extract:write",  # PAS de scope admin
    )
    return TenantContext(organization=org, api_key=key, raw_key_prefix="test")

app.dependency_overrides[get_tenant_context] = make_limited_tenant
client = TestClient(app)

r = client.get("/admin/config")
print("admin/config sans scope admin :", r.status_code, "(attendu: 403)")

app.dependency_overrides.clear()