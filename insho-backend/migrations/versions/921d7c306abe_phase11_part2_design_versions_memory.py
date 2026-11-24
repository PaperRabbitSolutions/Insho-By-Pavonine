"""phase11 part2 design versions and project memory

Revision ID: 921d7c306abe
Revises: 73fd6a4ce211
Create Date: 2026-11-24 11:34:50.004562

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '921d7c306abe'
down_revision = '73fd6a4ce211'
branch_labels = None
depends_on = None


_IMMUTABLE = (
    ("design_snapshots", "UPDATE OR DELETE", ()),
    ("design_snapshot_assets", "UPDATE OR DELETE", ()),
    ("design_versions", "UPDATE OR DELETE", ("created_by_user_id",)),
    ("design_version_reviews", "UPDATE OR DELETE", ()),
    ("design_activities", "UPDATE", ("actor_id", "execution_id")),
    ("project_memory_revisions", "UPDATE", ("changed_by_user_id",)),
    ("presentation_revisions", "UPDATE", ("created_by_user_id",)),
)


def protect_history():
    op.execute("""
        CREATE FUNCTION insho_design_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_data jsonb;
            new_data jsonb;
            field text;
        BEGIN
            IF TG_OP = 'UPDATE' AND TG_NARGS > 0 THEN
                old_data := to_jsonb(OLD);
                new_data := to_jsonb(NEW);
                IF (old_data - TG_ARGV) = (new_data - TG_ARGV) THEN
                    FOREACH field IN ARRAY TG_ARGV LOOP
                        IF (new_data -> field) IS DISTINCT FROM (old_data -> field)
                           AND (new_data -> field) IS DISTINCT FROM 'null'::jsonb THEN
                            RAISE EXCEPTION 'Design history is immutable' USING ERRCODE = '23514';
                        END IF;
                    END LOOP;
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'Design history is immutable' USING ERRCODE = '23514';
        END;
        $$
    """)
    for table, operations, fields in _IMMUTABLE:
        arguments = ", ".join(f"'{field}'" for field in fields)
        op.execute(f"CREATE TRIGGER protect_design_history BEFORE {operations} ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION insho_design_history_immutable({arguments})")
    op.execute("""
        CREATE TRIGGER protect_design_history BEFORE UPDATE OR DELETE ON presentation_checkpoints
        FOR EACH ROW WHEN (OLD.decided_at IS NOT NULL)
        EXECUTE FUNCTION insho_design_history_immutable('created_by_user_id', 'requested_by_user_id', 'decided_by_user_id')
    """)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint('uq_events_id_project', 'events', ['id', 'project_id'])
    op.create_table('design_snapshots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('scope_key', sa.String(length=36), nullable=False),
        sa.Column('state', postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='RESTRICT', name='fk_design_snapshots_project'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'project_id', 'scope_key', name='uq_design_snapshots_scope')
    )
    op.create_table('design_branches',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('scope_key', sa.String(length=36), nullable=False),
        sa.Column('source_version_id', sa.Uuid(), nullable=False),
        sa.Column('base_mainline_id', sa.Uuid(), nullable=False),
        sa.Column('parent_branch_id', sa.Uuid(), nullable=True),
        sa.Column('live_basis', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("scope_key = coalesce(event_id::text, 'project')", name='ck_design_branches_scope'),
        sa.CheckConstraint("status IN ('active', 'promoted', 'archived')", name='ck_design_branches_status'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_id', 'project_id'], ['events.id', 'events.project_id'],
                            name='fk_design_branches_event', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['parent_branch_id', 'project_id', 'scope_key'], ['design_branches.id', 'design_branches.project_id', 'design_branches.scope_key'], name='fk_design_branches_parent'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_design_branches_project', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'project_id', 'scope_key', name='uq_design_branches_scope')
    )
    op.create_index('ix_design_branches_project_scope_created', 'design_branches', ['project_id', 'scope_key', 'created_at'], unique=False)
    op.create_table('design_snapshot_assets',
        sa.Column('snapshot_id', sa.Uuid(), nullable=False),
        sa.Column('asset_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['design_snapshots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('snapshot_id', 'asset_id')
    )
    op.create_index(op.f('ix_design_snapshot_assets_asset_id'), 'design_snapshot_assets', ['asset_id'], unique=False)
    op.create_table('design_versions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('scope_key', sa.String(length=36), nullable=False),
        sa.Column('branch_id', sa.Uuid(), nullable=True),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Uuid(), nullable=True),
        sa.Column('based_on_id', sa.Uuid(), nullable=True),
        sa.Column('snapshot_id', sa.Uuid(), nullable=False),
        sa.Column('summary', sa.String(length=200), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("scope_key = coalesce(event_id::text, 'project') AND number > 0", name='ck_design_versions_scope_number'),
        sa.CheckConstraint('id <> parent_id AND id <> based_on_id', name='ck_design_versions_ancestry'),
        sa.ForeignKeyConstraint(['based_on_id', 'project_id', 'scope_key'], ['design_versions.id', 'design_versions.project_id', 'design_versions.scope_key'], name='fk_design_versions_based_on'),
        sa.ForeignKeyConstraint(['branch_id', 'project_id', 'scope_key'], ['design_branches.id', 'design_branches.project_id', 'design_branches.scope_key'], name='fk_design_versions_branch'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_id', 'project_id'], ['events.id', 'events.project_id'],
                            name='fk_design_versions_event', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['parent_id', 'project_id', 'scope_key'], ['design_versions.id', 'design_versions.project_id', 'design_versions.scope_key'], name='fk_design_versions_parent'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_design_versions_project', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['snapshot_id', 'project_id', 'scope_key'], ['design_snapshots.id', 'design_snapshots.project_id', 'design_snapshots.scope_key'], name='fk_design_versions_snapshot'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'project_id', 'scope_key', name='uq_design_versions_scope'),
        sa.UniqueConstraint('id', 'project_id', name='uq_design_versions_id_project'),
        sa.UniqueConstraint('project_id', 'scope_key', 'number', name='uq_design_versions_number')
    )
    op.create_index('ix_design_versions_head', 'design_versions', ['project_id', 'scope_key', 'branch_id', 'number'], unique=False)
    for column, name in (("source_version_id", "fk_design_branches_source"), ("base_mainline_id", "fk_design_branches_mainline")):
        op.create_foreign_key(name, "design_branches", "design_versions", [column, "project_id", "scope_key"],
                              ["id", "project_id", "scope_key"])
    op.create_table('design_activities',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('version_id', sa.Uuid(), nullable=True),
        sa.Column('branch_id', sa.Uuid(), nullable=True),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('execution_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.Uuid(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('details', postgresql.JSONB(), nullable=False),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['branch_id'], ['design_branches.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['execution_id'], ['ai_executions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['version_id'], ['design_versions.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_design_activities_branch', 'design_activities', ['branch_id'], unique=False)
    op.create_index('ix_design_activities_project_created', 'design_activities', ['project_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_design_activities_version', 'design_activities', ['version_id'], unique=False)
    op.create_table('design_version_reviews',
        sa.Column('version_id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_revision', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['presentation_id', 'presentation_revision'], ['presentation_revisions.presentation_id', 'presentation_revisions.version'], name='fk_design_review_presentation_revision', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['version_id', 'project_id'], ['design_versions.id', 'design_versions.project_id'],
                            name='fk_design_review_version_project', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['presentation_id', 'project_id'], ['presentations.id', 'presentations.project_id'],
                            name='fk_design_review_presentation_project', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('version_id'),
        sa.UniqueConstraint('presentation_id', 'presentation_revision', name='uq_design_review_presentation_revision')
    )
    op.create_table('project_memory_revisions',
        sa.Column('item_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('state', postgresql.JSONB(), nullable=False),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('changed_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('version > 0', name='ck_project_memory_revisions_version'),
        sa.ForeignKeyConstraint(['changed_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['item_id'], ['project_memory_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('item_id', 'version')
    )
    op.add_column('project_memory_items', sa.Column('category', sa.String(length=24), nullable=True))
    op.add_column('project_memory_items', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('project_memory_items', sa.Column('verification', sa.String(length=10), server_default='unverified', nullable=False))
    op.add_column('project_memory_items', sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('project_memory_items', sa.Column('linked_version_id', sa.Uuid(), nullable=True))
    op.add_column('project_memory_items', sa.Column('decision_id', sa.Uuid(), nullable=True))
    op.add_column('project_memory_items', sa.Column('execution_id', sa.Uuid(), nullable=True))
    op.create_index('ix_memory_items_project_category_active', 'project_memory_items', ['project_id', 'category', 'status', 'created_at'], unique=False)
    op.create_foreign_key('fk_memory_design_version', 'project_memory_items', 'design_versions', ['linked_version_id', 'project_id'],
                          ['id', 'project_id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_memory_decision', 'project_memory_items', 'presentation_checkpoints', ['decision_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('fk_memory_execution', 'project_memory_items', 'ai_executions', ['execution_id'], ['id'], ondelete='SET NULL')
    op.create_check_constraint('ck_memory_items_revision_verification', 'project_memory_items',
                               "version > 0 AND verification IN ('unverified', 'verified')")
    op.create_check_constraint('ck_memory_items_category', 'project_memory_items',
                               "category IN ('must_have','must_avoid','approved','rejected','client_preferences','design_dna',"
                               "'budget','venue_dimensions','guest_count','previous_decisions','vendor_constraints',"
                               "'production_constraints','safety_constraints')")
    op.execute("""
        UPDATE project_memory_items SET category = CASE kind
            WHEN 'preference' THEN 'client_preferences' WHEN 'decision' THEN 'previous_decisions' END
        WHERE kind IN ('preference', 'decision')
    """)
    op.execute("""
        INSERT INTO project_memory_revisions (item_id, version, state, action, changed_by_user_id, created_at)
        SELECT id, version, to_jsonb(item), 'baseline', created_by_user_id, updated_at FROM project_memory_items AS item
    """)
    protect_history()


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER protect_design_history ON presentation_checkpoints")
    for table, _, _ in _IMMUTABLE:
        op.execute(f"DROP TRIGGER protect_design_history ON {table}")
    op.execute("DROP FUNCTION insho_design_history_immutable()")
    for constraint in ('fk_memory_design_version', 'fk_memory_decision', 'fk_memory_execution'):
        op.drop_constraint(constraint, 'project_memory_items', type_='foreignkey')
    for constraint in ('ck_memory_items_category', 'ck_memory_items_revision_verification'):
        op.drop_constraint(constraint, 'project_memory_items', type_='check')
    op.drop_index('ix_memory_items_project_category_active', table_name='project_memory_items')
    op.drop_column('project_memory_items', 'execution_id')
    op.drop_column('project_memory_items', 'decision_id')
    op.drop_column('project_memory_items', 'linked_version_id')
    op.drop_column('project_memory_items', 'version')
    op.drop_column('project_memory_items', 'verification')
    op.drop_column('project_memory_items', 'description')
    op.drop_column('project_memory_items', 'category')
    op.drop_table('project_memory_revisions')
    op.drop_table('design_version_reviews')
    op.drop_index('ix_design_activities_version', table_name='design_activities')
    op.drop_index('ix_design_activities_project_created', table_name='design_activities')
    op.drop_index('ix_design_activities_branch', table_name='design_activities')
    op.drop_table('design_activities')
    op.drop_constraint('fk_design_branches_source', 'design_branches', type_='foreignkey')
    op.drop_constraint('fk_design_branches_mainline', 'design_branches', type_='foreignkey')
    op.drop_index('ix_design_versions_head', table_name='design_versions')
    op.drop_table('design_versions')
    op.drop_index(op.f('ix_design_snapshot_assets_asset_id'), table_name='design_snapshot_assets')
    op.drop_table('design_snapshot_assets')
    op.drop_index('ix_design_branches_project_scope_created', table_name='design_branches')
    op.drop_table('design_branches')
    op.drop_table('design_snapshots')
    op.drop_constraint('uq_events_id_project', 'events', type_='unique')
