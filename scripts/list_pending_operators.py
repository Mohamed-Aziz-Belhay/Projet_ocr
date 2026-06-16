"""
scripts/list_pending_operators.py

Usage:
    python scripts/list_pending_operators.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    from sqlalchemy import select
    from app.db.models.user import User
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User)
            .where(User.role == "operator", User.is_active == False)  # noqa: E712
            .order_by(User.created_at.desc())
        )

        users = result.scalars().all()

    if not users:
        print("[OK] No pending operators")
        return

    for user in users:
        print(f"{user.email} | {user.full_name or ''} | active={user.is_active}")


if __name__ == "__main__":
    asyncio.run(main())
