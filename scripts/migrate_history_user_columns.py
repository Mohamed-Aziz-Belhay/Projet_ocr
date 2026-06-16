"""
scripts/migrate_history_user_columns.py

Usage:
    python scripts/migrate_history_user_columns.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    from sqlalchemy import text
    from app.db.session import engine

    statements = [
        "ALTER TABLE extraction_history ADD COLUMN IF NOT EXISTS user_email VARCHAR(255)",
        "ALTER TABLE extraction_history ADD COLUMN IF NOT EXISTS user_role VARCHAR(50)",
        "CREATE INDEX IF NOT EXISTS ix_extraction_history_user_email ON extraction_history(user_email)",
        "CREATE INDEX IF NOT EXISTS ix_extraction_history_user_role ON extraction_history(user_role)",
    ]

    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))

    print("[OK] extraction_history.user_email and user_role are ready")


if __name__ == "__main__":
    asyncio.run(main())
