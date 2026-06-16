"""
scripts/migrate_history_details.py
Usage: python scripts/migrate_history_details.py
"""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
async def main() -> None:
    from sqlalchemy import text
    from app.db.session import engine
    statements = [
        """ALTER TABLE extraction_history ADD COLUMN IF NOT EXISTS user_role VARCHAR(50)""",
        """CREATE TABLE IF NOT EXISTS extraction_results (
            id VARCHAR(36) PRIMARY KEY,
            history_id VARCHAR(36),
            job_id VARCHAR(128) UNIQUE,
            raw_text TEXT,
            result_json JSONB,
            fields_json JSONB,
            diagnostics_json JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT fk_extraction_results_history FOREIGN KEY(history_id) REFERENCES extraction_history(id) ON DELETE CASCADE
        )""",
        """CREATE INDEX IF NOT EXISTS ix_extraction_results_job_id ON extraction_results(job_id)""",
        """CREATE INDEX IF NOT EXISTS ix_extraction_results_history_id ON extraction_results(history_id)""",
    ]
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))
    print('[OK] extraction_history.user_role and extraction_results are ready')
if __name__ == '__main__': asyncio.run(main())
