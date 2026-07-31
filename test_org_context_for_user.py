# test_org_context_for_user.py
import asyncio
from types import SimpleNamespace

from app.db.session import AsyncSessionLocal
from app.core.tenant import get_org_context_for_user


async def check_role(role: str, is_superuser: bool = False):
    fake_user = SimpleNamespace(
        id=f"test-user-{role}",
        role=role,
        is_superuser=is_superuser,
        organization_id=None,
    )
    async with AsyncSessionLocal() as db:
        tenant = await get_org_context_for_user(fake_user, db)
        await db.commit()

    print(f"{role:12s} -> scopes = {tenant.api_key.scopes}")
    print(f"{'':12s}    a le scope admin : {tenant.has_scope('admin')}")


async def main():
    await check_role("simple_user")
    await check_role("operator")
    await check_role("admin")


asyncio.run(main())