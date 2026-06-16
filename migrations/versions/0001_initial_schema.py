"""Initial enterprise schema

Revision ID: 0001
Revises: 
Create Date: 2025-01-01 00:00:00
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── organisations ────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id",                     sa.String(36),  primary_key=True),
        sa.Column("name",                   sa.String(200), nullable=False),
        sa.Column("slug",                   sa.String(100), nullable=False),
        sa.Column("contact_email",          sa.String(254)),
        sa.Column("description",            sa.Text),
        sa.Column("is_active",              sa.Boolean, default=True,  nullable=False),
        sa.Column("is_suspended",           sa.Boolean, default=False, nullable=False),
        sa.Column("suspension_reason",      sa.Text),
        sa.Column("quota_pages_per_month",  sa.Integer, default=10000),
        sa.Column("quota_jobs_per_month",   sa.Integer, default=1000),
        sa.Column("quota_templates",        sa.Integer, default=50),
        sa.Column("usage_pages_this_month", sa.Integer, default=0),
        sa.Column("usage_jobs_this_month",  sa.Integer, default=0),
        sa.Column("data_retention_days",    sa.Integer, default=90),
        sa.Column("gdpr_dpo_email",         sa.String(254)),
        sa.Column("created_at",             sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",             sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    # ── api_keys ─────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id",               sa.String(36),  primary_key=True),
        sa.Column("name",             sa.String(200), nullable=False),
        sa.Column("key_hash",         sa.String(64),  nullable=False),
        sa.Column("key_prefix",       sa.String(20),  nullable=False),
        sa.Column("organization_id",  sa.String(36),  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scopes",           sa.Text,        default="extract:read,extract:write"),
        sa.Column("is_active",        sa.Boolean,     default=True),
        sa.Column("is_read_only",     sa.Boolean,     default=False),
        sa.Column("rate_limit_rpm",   sa.Integer,     default=0),
        sa.Column("expires_at",       sa.DateTime(timezone=True)),
        sa.Column("ip_whitelist",     sa.Text),
        sa.Column("last_used_at",     sa.DateTime(timezone=True)),
        sa.Column("total_requests",   sa.Integer,     default=0),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_api_keys_key_hash",       "api_keys", ["key_hash"],       unique=True)
    op.create_index("ix_api_keys_organization_id","api_keys", ["organization_id"])

    # ── jobs ─────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id",                  sa.String(36),  primary_key=True),
        sa.Column("organization_id",     sa.String(36),  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("api_key_id",          sa.String(36),  sa.ForeignKey("api_keys.id",      ondelete="SET NULL")),
        sa.Column("status",              sa.String(20),  default="queued"),
        sa.Column("progress_pct",        sa.Integer,     default=0),
        sa.Column("error",               sa.Text),
        sa.Column("template_id",         sa.String(100)),
        sa.Column("engine_used",         sa.String(50)),
        sa.Column("file_name",           sa.String(500)),
        sa.Column("file_size_bytes",     sa.Integer),
        sa.Column("page_count",          sa.Integer,     default=1),
        sa.Column("result_path",         sa.Text),
        sa.Column("global_confidence",   sa.Float),
        sa.Column("field_count",         sa.Integer,     default=0),
        sa.Column("processing_time_ms",  sa.Integer),
        sa.Column("celery_task_id",      sa.String(50)),
        sa.Column("retry_count",         sa.Integer,     default=0),
        sa.Column("webhook_url",         sa.Text),
        sa.Column("webhook_delivered",   sa.Boolean,     default=False),
        sa.Column("webhook_delivered_at",sa.DateTime(timezone=True)),
        sa.Column("purged_at",           sa.DateTime(timezone=True)),
        sa.Column("is_purged",           sa.Boolean,     default=False),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_organization_id", "jobs", ["organization_id"])
    op.create_index("ix_jobs_status",          "jobs", ["status"])
    op.create_index("ix_jobs_celery_task_id",  "jobs", ["celery_task_id"])

    # ── audit_logs ───────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id",              sa.String(36),  primary_key=True),
        sa.Column("organization_id", sa.String(36),  sa.ForeignKey("organizations.id", ondelete="SET NULL")),
        sa.Column("api_key_prefix",  sa.String(20)),
        sa.Column("ip_address",      sa.String(45)),
        sa.Column("event_type",      sa.String(60),  nullable=False),
        sa.Column("resource_type",   sa.String(60)),
        sa.Column("resource_id",     sa.String(100)),
        sa.Column("http_method",     sa.String(10)),
        sa.Column("http_path",       sa.String(500)),
        sa.Column("http_status",     sa.Integer),
        sa.Column("details",         sa.JSON),
        sa.Column("is_anonymized",   sa.Boolean,     default=False),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",      sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_organization_id", "audit_logs", ["organization_id"])
    op.create_index("ix_audit_logs_event_type",      "audit_logs", ["event_type"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("jobs")
    op.drop_table("api_keys")
    op.drop_table("organizations")