"""Add event venue links and structured project memory items.

Revision ID: f42b8c7d190e
Revises: ed7a2c91b405
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f42b8c7d190e"
down_revision: Union[str, Sequence[str], None] = "ed7a2c91b405"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("venue_id", sa.Uuid(), nullable=True))
    op.create_index("ix_events_venue_id", "events", ["venue_id"])
    op.create_foreign_key(
        "fk_events_venue_id_venues",
        "events",
        "venues",
        ["venue_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "project_memory_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_type", sa.String(length=40), server_default="user", nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('decision', 'preference', 'constraint', 'requirement', 'fact', 'note')",
            name="ck_project_memory_items_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_project_memory_items_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content) = 'object' AND content <> '{}'::jsonb",
            name="ck_project_memory_items_content_object",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE", name="fk_project_memory_items_project_id_projects"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL", name="fk_project_memory_items_event_id_events"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL", name="fk_project_memory_items_created_by_user_id_users"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_memory_items_project_id", "project_memory_items", ["project_id"])
    op.create_index("ix_project_memory_items_event_id", "project_memory_items", ["event_id"])
    op.create_index("ix_project_memory_items_created_by_user_id", "project_memory_items", ["created_by_user_id"])


def downgrade() -> None:
    op.drop_table("project_memory_items")
    op.drop_constraint("fk_events_venue_id_venues", "events", type_="foreignkey")
    op.drop_index("ix_events_venue_id", table_name="events")
    op.drop_column("events", "venue_id")
