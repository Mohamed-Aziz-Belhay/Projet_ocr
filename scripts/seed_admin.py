"""
scripts/seed_admin.py

Create an admin user for local/PFE demo.

Usage from project root:
    python scripts/seed_admin.py

Optional env:
    ADMIN_EMAIL=admin@arabsoft.com.tn
    ADMIN_PASSWORD=Admin12345!
    ADMIN_FULL_NAME="Arabsoft Admin"
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import os

from app.db.session import AsyncSessionLocal, create_all_tables
from app.services.auth_service import AuthService


async def main() -> None:
    await create_all_tables()

    email = os.getenv("ADMIN_EMAIL", "admin@arabsoft.com.tn")
    password = os.getenv("ADMIN_PASSWORD", "Admin12345!")
    full_name = os.getenv("ADMIN_FULL_NAME", "Arabsoft Admin")

    async with AsyncSessionLocal() as db:
        auth = AuthService(db)

        existing = await auth.get_user_by_email(email)

        if existing:
            print(f"[OK] Admin already exists: {email}")
            return

        await auth.create_user(
            email=email,
            password=password,
            full_name=full_name,
            role="admin",
            is_superuser=True,
        )
        await db.commit()

    print("[OK] Admin user created")
    print(f"email: {email}")
    print(f"password: {password}")


if __name__ == "__main__":
    asyncio.run(main())
