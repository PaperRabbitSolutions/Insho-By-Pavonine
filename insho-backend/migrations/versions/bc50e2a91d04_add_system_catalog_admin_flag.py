"""Add a separate role for shared system catalog writes.

Revision ID: bc50e2a91d04
Revises: 9a20c7f4d13b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bc50e2a91d04"
down_revision: Union[str, Sequence[str], None] = "9a20c7f4d13b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_system_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_system_admin")
