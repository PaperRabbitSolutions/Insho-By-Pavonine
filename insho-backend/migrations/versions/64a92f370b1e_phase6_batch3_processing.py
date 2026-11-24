"""Add media processing state and asset derivatives.

Revision ID: 64a92f370b1e
Revises: ab61d3f20c84
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "64a92f370b1e"
down_revision: Union[str, Sequence[str], None] = "ab61d3f20c84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_assets_processing_status", "assets", type_="check")
    op.create_check_constraint(
        "ck_assets_processing_status",
        "assets",
        "processing_status IN ('not_started', 'pending', 'processing', 'complete', 'failed', 'not_applicable')",
    )
    op.add_column(
        "assets",
        sa.Column("duplicate_of_asset_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_assets_duplicate_of_asset_id", "assets", ["duplicate_of_asset_id"])
    op.create_foreign_key(
        "fk_assets_duplicate_of_asset_id_assets", "assets", "assets",
        ["duplicate_of_asset_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("assets", sa.Column("processing_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("assets", sa.Column("processing_error_code", sa.String(length=50), nullable=True))
    op.add_column("assets", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assets", sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assets", sa.Column("media_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute(
        "UPDATE assets SET processing_status = 'not_applicable' "
        "WHERE mime_type NOT IN ('image/jpeg', 'image/png', 'image/webp', 'video/mp4') "
        "AND processing_status = 'not_started'"
    )

    op.create_table(
        "asset_derivatives",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("storage_key", sa.String(length=32), nullable=False),
        sa.Column("storage_provider", sa.String(length=30), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_extension", sa.String(length=10), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('thumbnail', 'preview', 'poster')", name="ck_asset_derivatives_kind"),
        sa.CheckConstraint("file_size > 0", name="ck_asset_derivatives_file_size_positive"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "kind", name="uq_asset_derivatives_asset_kind"),
        sa.UniqueConstraint("storage_key", name="uq_asset_derivatives_storage_key"),
    )


def downgrade() -> None:
    op.drop_table("asset_derivatives")
    op.execute(
        "UPDATE assets SET processing_status = 'not_started' "
        "WHERE processing_status IN ('processing', 'not_applicable')"
    )
    op.drop_column("assets", "media_metadata")
    op.drop_column("assets", "processing_completed_at")
    op.drop_column("assets", "processing_started_at")
    op.drop_column("assets", "processing_error_code")
    op.drop_column("assets", "processing_attempts")
    op.drop_constraint("fk_assets_duplicate_of_asset_id_assets", "assets", type_="foreignkey")
    op.drop_index("ix_assets_duplicate_of_asset_id", table_name="assets")
    op.drop_column("assets", "duplicate_of_asset_id")
    op.drop_constraint("ck_assets_processing_status", "assets", type_="check")
    op.create_check_constraint(
        "ck_assets_processing_status", "assets",
        "processing_status IN ('not_started', 'pending', 'complete', 'failed')",
    )
