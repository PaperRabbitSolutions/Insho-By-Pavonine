"""Persist versioned core design-intelligence outputs.

Revision ID: 93b4c5d6e7f8
Revises: 7f1a9b2c3d4e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "93b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "7f1a9b2c3d4e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "design_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("event_scope_key", sa.String(length=36), nullable=False),
        sa.Column("module", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("based_on", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=30), server_default="ai_generated", nullable=False),
        sa.Column("ai_execution_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("module IN ('mood', 'style', 'plan', 'vision', 'elements')", name="ck_design_artifacts_module"),
        sa.CheckConstraint("status IN ('draft', 'approved', 'archived')", name="ck_design_artifacts_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ai_execution_id"], ["ai_executions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "event_scope_key", "module", "version", name="uq_design_artifacts_version"),
    )
    op.create_index("ix_design_artifacts_project_id", "design_artifacts", ["project_id"])
    op.create_index("ix_design_artifacts_event_id", "design_artifacts", ["event_id"])
    op.create_index("ix_design_artifacts_module", "design_artifacts", ["module"])
    op.create_index("ix_design_artifacts_ai_execution_id", "design_artifacts", ["ai_execution_id"])
    op.create_index("ix_design_artifacts_created_by_user_id", "design_artifacts", ["created_by_user_id"])

    op.create_table(
        "design_elements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("source", sa.String(length=30), server_default="ai_suggested", nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("category IN ('floral', 'decor', 'furniture', 'lighting', 'scenic', 'architectural', 'stage', 'fabric', 'flooring', 'structure', 'prop', 'other')", name="ck_design_elements_category"),
        sa.CheckConstraint("source IN ('ai_suggested', 'user_confirmed')", name="ck_design_elements_source"),
        sa.ForeignKeyConstraint(["artifact_id"], ["design_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_design_elements_artifact_id", "design_elements", ["artifact_id"])
    op.create_index("ix_design_elements_project_id", "design_elements", ["project_id"])
    op.create_index("ix_design_elements_event_id", "design_elements", ["event_id"])

    op.create_table(
        "design_artifact_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="reference", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('reference', 'generated')", name="ck_design_artifact_assets_role"),
        sa.ForeignKeyConstraint(["artifact_id"], ["design_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "asset_id", name="uq_design_artifact_asset"),
    )
    op.create_index("ix_design_artifact_assets_artifact_id", "design_artifact_assets", ["artifact_id"])
    op.create_index("ix_design_artifact_assets_asset_id", "design_artifact_assets", ["asset_id"])


def downgrade() -> None:
    op.drop_table("design_artifact_assets")
    op.drop_table("design_elements")
    op.drop_table("design_artifacts")
