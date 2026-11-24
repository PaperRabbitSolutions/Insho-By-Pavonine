"""Prevent case-variant duplicate user identities.

Revision ID: c2a7f4d9e116
Revises: bc50e2a91d04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2a7f4d9e116"
down_revision: Union[str, Sequence[str], None] = "bc50e2a91d04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
