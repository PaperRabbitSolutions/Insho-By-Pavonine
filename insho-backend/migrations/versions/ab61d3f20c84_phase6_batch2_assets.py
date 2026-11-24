"""Add managed assets and logical folders.

Revision ID: ab61d3f20c84
Revises: f42b8c7d190e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ab61d3f20c84"
down_revision: Union[str, Sequence[str], None] = "f42b8c7d190e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset_folders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("parent_folder_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_folder_id"], ["asset_folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_folders_organization_id", "asset_folders", ["organization_id"])
    op.create_index(
        "ix_asset_folders_scope", "asset_folders",
        ["organization_id", "workspace_id", "project_id", "parent_folder_id"],
    )
    op.create_index(
        "uq_asset_folders_scope_parent_name",
        "asset_folders",
        [
            sa.text("organization_id"),
            sa.text("COALESCE(workspace_id::text, '')"),
            sa.text("COALESCE(project_id::text, '')"),
            sa.text("COALESCE(parent_folder_id::text, '')"),
            sa.text("lower(name)"),
        ],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(length=32), nullable=False),
        sa.Column("storage_provider", sa.String(length=30), nullable=False),
        sa.Column("mime_type", sa.String(length=150), nullable=False),
        sa.Column("file_extension", sa.String(length=16), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=30), server_default="other", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("processing_status", sa.String(length=20), server_default="not_started", nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('image', 'video', 'document', 'design', 'floor_plan', 'venue_reference', 'inspiration', 'audio', 'other')",
            name="ck_assets_category",
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name="ck_assets_status"),
        sa.CheckConstraint("file_size > 0", name="ck_assets_file_size_positive"),
        sa.CheckConstraint(
            "processing_status IN ('not_started', 'pending', 'complete', 'failed')",
            name="ck_assets_processing_status",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["folder_id"], ["asset_folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_assets_storage_key"),
    )
    for column in ("organization_id", "workspace_id", "project_id", "event_id", "folder_id", "uploaded_by_user_id"):
        op.create_index(f"ix_assets_{column}", "assets", [column])
    op.create_index("ix_assets_org_created", "assets", ["organization_id", "created_at"])
    op.create_index("ix_assets_scope_status", "assets", ["organization_id", "project_id", "event_id", "status"])
    op.create_index("ix_assets_folder", "assets", ["folder_id", "status"])


def downgrade() -> None:
    op.drop_table("assets")
    op.drop_table("asset_folders")
