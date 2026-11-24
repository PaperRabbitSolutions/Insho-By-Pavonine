"""Complete Phase 4 project and event management fields.

Revision ID: ed7a2c91b405
Revises: c2a7f4d9e116
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ed7a2c91b405"
down_revision: Union[str, Sequence[str], None] = "c2a7f4d9e116"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("events", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_index("ix_events_project_id", "events", ["project_id"])
    op.create_foreign_key(
        "fk_events_project_id_projects",
        "events",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "events", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "events", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("events", sa.Column("budget", sa.Numeric(14, 2), nullable=True))
    op.add_column("events", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column(
        "events",
        sa.Column("status", sa.String(30), server_default="draft", nullable=False),
    )
    op.add_column(
        "events",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("events", "version")
    op.drop_column("events", "status")
    op.drop_column("events", "currency")
    op.drop_column("events", "budget")
    op.drop_column("events", "ends_at")
    op.drop_column("events", "starts_at")
    op.drop_constraint("fk_events_project_id_projects", "events", type_="foreignkey")
    op.drop_index("ix_events_project_id", table_name="events")
    op.drop_column("events", "project_id")
    op.drop_column("projects", "version")
