#!/usr/bin/env python3
"""
scripts/setup_enterprise.py — FIXED
FIX #6  : utilise Alembic upgrade head au lieu de create_all_tables()
          → les versions de migration sont correctement enregistrées
FIX #8  : crée script.py.mako si absent
"""
from __future__ import annotations
import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def check_env() -> list:
    return [k for k in ["SECRET_KEY", "SUPER_ADMIN_KEY", "DATABASE_URL"] if not os.environ.get(k)]


def run_alembic_migrations() -> None:
    """FIX #6 : Alembic upgrade head (pas create_all_tables)."""
    print("  → Running: alembic upgrade head")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  ✗ Alembic failed:\n{result.stderr}")
        # Fallback pour SQLite / tests sans alembic installé
        print("  → Fallback: create_all_tables()")
        asyncio.run(_fallback_create_tables())
    else:
        print(f"  ✓ Migrations applied:\n{result.stdout.strip()}")


async def _fallback_create_tables() -> None:
    from app.db.session import create_all_tables
    await create_all_tables()
    print("  ✓ Tables created via SQLAlchemy")


async def run_setup(reset: bool = False) -> None:
    from dotenv import load_dotenv
    load_dotenv()

    print("\n🔍 Checking environment variables...")
    missing = check_env()
    if missing:
        print(f"  ✗ Missing: {missing}")
        print("  → Copy .env.example to .env and fill in the values")
        sys.exit(1)
    print("  ✓ All required env vars present")

    # FIX #8 : créer script.py.mako si absent
    mako_path = Path("migrations/script.py.mako")
    if not mako_path.exists():
        print("\n📝 Creating migrations/script.py.mako...")
        mako_path.write_text(
            '"""${message}\n\nRevision ID: ${up_revision}\n'
            'Revises: ${down_revision | comma,n}\n'
            'Create Date: ${create_date}\n"""\n'
            'from __future__ import annotations\n'
            'from alembic import op\nimport sqlalchemy as sa\n'
            '${imports if imports else ""}\n\n'
            'revision = ${repr(up_revision)}\n'
            'down_revision = ${repr(down_revision)}\n'
            'branch_labels = ${repr(branch_labels)}\n'
            'depends_on = ${repr(depends_on)}\n\n\n'
            'def upgrade() -> None:\n    ${upgrades if upgrades else "pass"}\n\n\n'
            'def downgrade() -> None:\n    ${downgrades if downgrades else "pass"}\n'
        )
        print("  ✓ script.py.mako created")

    # FIX #6 : Alembic au lieu de create_all_tables
    print("\n🗄️  Running database migrations (Alembic)...")
    if reset:
        print("  ⚠️  RESET: dropping all tables first")
        await _fallback_create_tables.__wrapped__() if hasattr(_fallback_create_tables, '__wrapped__') else None
        from app.db.session import drop_all_tables
        await drop_all_tables()
        print("  ✓ Tables dropped")
    run_alembic_migrations()

    # Create default organisation
    print("\n🏢 Creating default organisation...")
    from app.db.session import AsyncSessionLocal
    from app.services.tenant_service import TenantService
    from sqlalchemy import select
    from app.db.models.organization import Organization

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(Organization).where(Organization.slug == "default")
        )).scalar_one_or_none()

        if existing:
            print(f"  ℹ️  Default org already exists (id={existing.id[:8]}...)")
            org = existing
        else:
            svc = TenantService(db)
            org = await svc.create_org(
                name="Default Organisation",
                contact_email=os.environ.get("GDPR_DPO_EMAIL", "admin@localhost"),
                quota_pages=100000, quota_jobs=10000,
                retention_days=int(os.environ.get("DATA_RETENTION_DAYS", "90")),
            )
            await db.commit()
            print(f"  ✓ Created: slug={org.slug}, id={org.id[:8]}...")

        print("\n🔑 Creating initial API key...")
        svc = TenantService(db)
        key_obj, raw_key = await svc.create_api_key(
            org_id=org.id,
            name="Initial Admin Key",
            scopes="extract:read,extract:write,templates:read,templates:write,admin",
        )
        await db.commit()

    # MinIO buckets
    print("\n📦 Verifying MinIO/S3 buckets...")
    s3_url = os.environ.get("S3_ENDPOINT_URL")
    if s3_url:
        try:
            import boto3
            s3 = boto3.client(
                "s3", endpoint_url=s3_url,
                aws_access_key_id=os.environ.get("S3_ACCESS_KEY"),
                aws_secret_access_key=os.environ.get("S3_SECRET_KEY"),
                use_ssl=os.environ.get("S3_USE_SSL", "false").lower() == "true",
            )
            for bucket in ["ocr-uploads", "ocr-results"]:
                try:
                    s3.head_bucket(Bucket=bucket)
                    print(f"  ✓ Bucket '{bucket}' exists")
                except Exception:
                    s3.create_bucket(Bucket=bucket)
                    print(f"  ✓ Bucket '{bucket}' created")
        except Exception as exc:
            print(f"  ⚠️  MinIO not reachable: {exc} (fallback: local storage)")
    else:
        print("  ℹ️  S3_ENDPOINT_URL not set — using local storage")

    # Templates
    print("\n📋 Loading templates...")
    from app.services.template_service import TemplateService
    templates = TemplateService().list_all()
    print(f"  ✓ {len(templates)} templates: {[t.id for t in templates]}")

    port = os.environ.get("PORT", "8000")
    print("\n" + "="*60)
    print("✅  Setup complete!\n")
    print(f"  API             : http://localhost:{port}/docs")
    print(f"  Health          : http://localhost:{port}/health")
    print(f"  Organisation    : {org.slug} ({org.id[:8]}...)")
    print()
    print(f"  ⚠️  API Key (shown once — save it now):")
    print(f"  {raw_key}")
    print()
    print(f"  Add to your requests:")
    print(f'  curl -H "X-API-Key: {raw_key}" http://localhost:{port}/health')
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Drop tables first (dev only)")
    args = parser.parse_args()
    if args.reset:
        if input("⚠️  Drop all tables? Type 'yes': ").lower() != "yes":
            sys.exit(0)
    asyncio.run(run_setup(reset=args.reset))


if __name__ == "__main__":
    main()
