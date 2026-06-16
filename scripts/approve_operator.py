"""
scripts/approve_operator.py

Usage:
    python scripts/approve_operator.py user@example.com
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/approve_operator.py user@example.com")
        raise SystemExit(1)

    email = sys.argv[1].strip().lower()

    from sqlalchemy import select
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            print(f"[ERROR] User not found: {email}")
            raise SystemExit(2)

        user.role = "operator"
        user.is_active = True
        await db.commit()

    print(f"[OK] Operator approved: {email}")


if __name__ == "__main__":
    asyncio.run(main())
