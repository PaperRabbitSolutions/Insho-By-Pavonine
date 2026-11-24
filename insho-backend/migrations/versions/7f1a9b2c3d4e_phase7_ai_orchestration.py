"""Add tenant-scoped AI execution history.

Revision ID: 7f1a9b2c3d4e
Revises: 64a92f370b1e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "7f1a9b2c3d4e"
down_revision: Union[str, Sequence[str], None] = "64a92f370b1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("task", sa.String(length=80), nullable=False),
        sa.Column("feature", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=150), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("output_format", sa.String(length=20), server_default="text", nullable=False),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fallback_from", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')", name="ck_ai_executions_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "organization_id", "idempotency_key", name="uq_ai_execution_idempotency"),
    )
    op.create_index("ix_ai_executions_request_id", "ai_executions", ["request_id"])
    op.create_index("ix_ai_executions_user_id", "ai_executions", ["user_id"])
    op.create_index("ix_ai_executions_organization_id", "ai_executions", ["organization_id"])
    op.create_index("ix_ai_executions_workspace_id", "ai_executions", ["workspace_id"])
    op.create_index("ix_ai_executions_project_id", "ai_executions", ["project_id"])
    op.create_index("ix_ai_executions_org_created", "ai_executions", ["organization_id", "created_at"])
    op.create_index("ix_ai_executions_project_created", "ai_executions", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("ai_executions")
