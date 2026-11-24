"""Persist spatial recommendation reviews and canonical application receipts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "02e73da81bf4"
down_revision = "eb4c51ad9027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "spatial_design_reviews",
        sa.Column("scene_id", sa.Uuid(), sa.ForeignKey("spatial_scenes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("recommendation_id", sa.Uuid(), primary_key=True),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("context_version", sa.String(64), nullable=False),
        sa.Column("source", JSONB(), nullable=False),
        sa.Column("action", JSONB()),
        sa.Column("policy_fingerprint", sa.String(64)),
        sa.Column("outcome", JSONB()),
        sa.Column("reviewed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('validated', 'applied', 'rejected')", name="ck_spatial_design_review_status"),
        sa.CheckConstraint("base_revision > 0", name="ck_spatial_design_review_revision"),
        sa.CheckConstraint(
            "outcome IS NOT NULL AND ((status = 'rejected' AND action IS NULL AND policy_fingerprint IS NULL) "
            "OR (status IN ('validated', 'applied') AND action IS NOT NULL AND policy_fingerprint IS NOT NULL))",
            name="ck_spatial_design_review_payload",
        ),
    )


def downgrade():
    op.drop_table("spatial_design_reviews")
