"""phase11 part1 client presentations

Revision ID: 73fd6a4ce211
Revises: 4b9d5a732e10
Create Date: 2026-11-24 11:05:12.733601

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '73fd6a4ce211'
down_revision: Union[str, Sequence[str], None] = '4b9d5a732e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('presentations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('alternative_of_id', sa.Uuid(), nullable=True),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('id <> alternative_of_id', name='ck_presentations_alternative'),
        sa.ForeignKeyConstraint(['alternative_of_id', 'project_id'], ['presentations.id', 'presentations.project_id'], name='fk_presentations_alternative_project'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], name='fk_presentations_event', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_presentations_project', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'project_id', name='uq_presentations_id_project')
    )
    op.create_index('ix_presentations_alternative_of_id', 'presentations', ['alternative_of_id'], unique=False)
    op.create_index(op.f('ix_presentations_event_id'), 'presentations', ['event_id'], unique=False)
    op.create_index('ix_presentations_project_event_created', 'presentations', ['project_id', 'event_id', 'created_at', 'id'], unique=False)
    op.create_table('presentation_revisions',
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('sheet', postgresql.JSONB(), nullable=False),
        sa.Column('evidence', postgresql.JSONB(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('version > 0', name='ck_presentation_revisions_version'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('presentation_id', 'version')
    )
    op.create_table('presentation_assets',
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('asset_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['presentation_id', 'revision'], ['presentation_revisions.presentation_id', 'presentation_revisions.version'], name='fk_presentation_assets_revision', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('presentation_id', 'revision', 'asset_id')
    )
    op.create_index('ix_presentation_assets_asset_id', 'presentation_assets', ['asset_id'], unique=False)
    op.create_table('presentation_checkpoints',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('request_comment', sa.Text(), nullable=True),
        sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.CheckConstraint("(status = 'draft' AND requested_at IS NULL AND decided_at IS NULL) OR (status = 'pending' AND requested_at IS NOT NULL AND decided_at IS NULL) OR (status IN ('approved', 'rejected', 'revision_requested') AND requested_at IS NOT NULL AND decided_at IS NOT NULL)", name='ck_presentation_checkpoints_state'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['presentation_id', 'revision'], ['presentation_revisions.presentation_id', 'presentation_revisions.version'], name='fk_presentation_checkpoints_revision', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_presentation_checkpoints_revision', 'presentation_checkpoints', ['presentation_id', 'revision', 'created_at', 'id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_presentation_checkpoints_revision', table_name='presentation_checkpoints')
    op.drop_table('presentation_checkpoints')
    op.drop_index('ix_presentation_assets_asset_id', table_name='presentation_assets')
    op.drop_table('presentation_assets')
    op.drop_table('presentation_revisions')
    op.drop_index('ix_presentations_project_event_created', table_name='presentations')
    op.drop_index(op.f('ix_presentations_event_id'), table_name='presentations')
    op.drop_index('ix_presentations_alternative_of_id', table_name='presentations')
    op.drop_table('presentations')
