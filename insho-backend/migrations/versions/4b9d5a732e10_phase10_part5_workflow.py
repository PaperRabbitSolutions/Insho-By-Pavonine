"""Add production workflow history and same-scene predecessor references.

Revision ID: 4b9d5a732e10
Revises: 8d31b6e2f4a7
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "4b9d5a732e10"
down_revision = "8d31b6e2f4a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("production_records", sa.Column("supersedes_id", sa.Uuid(), nullable=True))
    op.create_check_constraint("ck_production_records_predecessor", "production_records", "supersedes_id <> id")
    op.create_foreign_key("fk_production_records_predecessor_scene", "production_records", "production_records",
                          ["supersedes_id", "scene_id"], ["id", "scene_id"])
    op.create_index("ix_production_records_supersedes_id", "production_records", ["supersedes_id"])
    op.create_table(
        "production_workflow_revisions",
        sa.Column("record_id", sa.Uuid(), sa.ForeignKey("production_records.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tasks", JSONB(), nullable=False),
        sa.Column("readiness_basis", JSONB(), nullable=True),
        sa.Column("evidence_reference", sa.Text(), nullable=True),
        sa.Column("changed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_production_workflow_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'design_approved', 'technical_review', 'production_ready', 'in_fabrication', "
            "'on_site', 'installed', 'inspected', 'live', 'dismantling', 'archived')",
            name="ck_production_workflow_status",
        ),
        sa.CheckConstraint("jsonb_typeof(tasks) = 'array'", name="ck_production_workflow_tasks"),
    )


def downgrade():
    op.drop_table("production_workflow_revisions")
    op.drop_index("ix_production_records_supersedes_id", table_name="production_records")
    op.drop_constraint("fk_production_records_predecessor_scene", "production_records", type_="foreignkey")
    op.drop_constraint("ck_production_records_predecessor", "production_records", type_="check")
    op.drop_column("production_records", "supersedes_id")
