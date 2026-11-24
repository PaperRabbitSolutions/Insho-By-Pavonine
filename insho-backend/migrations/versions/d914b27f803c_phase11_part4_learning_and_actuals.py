"""phase11 part4 learning and actuals

Revision ID: d914b27f803c
Revises: 67e52ccf940a
Create Date: 2026-11-24 12:18:44.786956

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd914b27f803c'
down_revision: Union[str, Sequence[str], None] = '67e52ccf940a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('learning_actuals',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('design_version_id', sa.Uuid(), nullable=True),
        sa.Column('snapshot_id', sa.Uuid(), nullable=True),
        sa.Column('supersedes_id', sa.Uuid(), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('origin', sa.String(length=12), nullable=False),
        sa.Column('source_reference', sa.String(length=2000), nullable=True),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('derived', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('occurred_on', sa.Date(), nullable=False),
        sa.Column('recorded_by_id', sa.Uuid(), nullable=True),
        sa.Column('verified_by_id', sa.Uuid(), nullable=True),
        sa.Column('review_note', sa.String(length=2000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("(status = 'draft' AND verified_at IS NULL AND review_note IS NULL) OR (status = 'verified' AND verified_at IS NOT NULL AND review_note IS NOT NULL)", name='ck_learning_actual_status'),
        sa.CheckConstraint("origin IN ('observed', 'reported') AND data->>'kind' = kind", name='ck_learning_actual_origin'),
        sa.CheckConstraint('(design_version_id IS NULL) = (snapshot_id IS NULL)', name='ck_learning_actual_snapshot'),
        sa.CheckConstraint('revision > 0 AND supersedes_id IS DISTINCT FROM id', name='ck_learning_actual_revision'),
        sa.ForeignKeyConstraint(['design_version_id', 'project_id'], ['design_versions.id', 'design_versions.project_id'], name='fk_learning_actual_version', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['event_id', 'project_id'], ['events.id', 'events.project_id'], name='fk_learning_actual_event', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['recorded_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['design_snapshots.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['supersedes_id', 'event_id', 'project_id'], ['learning_actuals.id', 'learning_actuals.event_id', 'learning_actuals.project_id'], name='fk_learning_actual_predecessor', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['verified_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'event_id', 'project_id', name='uq_learning_actual_scope')
    )
    op.create_index('ix_learning_actual_event_history', 'learning_actuals', ['project_id', 'event_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_learning_actual_inputs', 'learning_actuals', ['project_id', 'event_id', 'design_version_id', 'status', 'kind'], unique=False)
    op.create_index('uq_learning_actual_verified_correction', 'learning_actuals', ['supersedes_id'], unique=True, postgresql_where=sa.text("status = 'verified' AND supersedes_id IS NOT NULL"))
    op.create_table('learning_reports',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('event_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('design_version_id', sa.Uuid(), nullable=True),
        sa.Column('snapshot_id', sa.Uuid(), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=True),
        sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('method_version', sa.String(length=24), nullable=False),
        sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('ai_execution_id', sa.Uuid(), nullable=True),
        sa.Column('initiated_by_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("(kind = 'reconciliation' AND event_id IS NOT NULL AND design_version_id IS NOT NULL AND snapshot_id IS NOT NULL) OR (kind = 'insights' AND event_id IS NULL AND design_version_id IS NULL AND snapshot_id IS NULL)", name='ck_learning_report_kind'),
        sa.ForeignKeyConstraint(['ai_execution_id'], ['ai_executions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['design_version_id', 'project_id'], ['design_versions.id', 'design_versions.project_id'], name='fk_learning_report_version', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['event_id', 'project_id'], ['events.id', 'events.project_id'], name='fk_learning_report_event', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['initiated_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['design_snapshots.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'project_id', name='uq_learning_report_project'),
        sa.UniqueConstraint('project_id', 'kind', 'idempotency_key', name='uq_learning_report_idempotency')
    )
    op.create_index('ix_learning_report_history', 'learning_reports', ['project_id', 'kind', 'created_at', 'id'], unique=False)
    op.create_table('learning_dispositions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('report_id', sa.Uuid(), nullable=False),
        sa.Column('insight_id', sa.Uuid(), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('decision', sa.String(length=24), nullable=False),
        sa.Column('note', sa.String(length=2000), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("decision IN ('accepted_for_review','dismissed','deferred')", name='ck_learning_disposition_decision'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['report_id', 'project_id'], ['learning_reports.id', 'learning_reports.project_id'], name='fk_learning_disposition_report', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_learning_disposition_history', 'learning_dispositions', ['report_id', 'insight_id', 'created_at', 'id'], unique=False)
    op.execute("""
        CREATE FUNCTION insho_validate_learning_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM projects p WHERE p.id = NEW.project_id AND p.organization_id = NEW.organization_id)
                OR (NEW.design_version_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM design_versions v WHERE v.id = NEW.design_version_id AND v.project_id = NEW.project_id
                    AND v.event_id = NEW.event_id AND v.snapshot_id = NEW.snapshot_id
                )) THEN
                RAISE EXCEPTION 'Learning source does not match its project, event and snapshot' USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'learning_actuals' THEN
                IF NOT EXISTS (SELECT 1 FROM events e WHERE e.id = NEW.event_id AND e.project_id = NEW.project_id
                               AND e.organization_id = NEW.organization_id) THEN
                    RAISE EXCEPTION 'Actual event does not match its tenant' USING ERRCODE = '23514';
                END IF;
                IF TG_OP = 'UPDATE' AND
                    (OLD.id, OLD.project_id, OLD.organization_id, OLD.event_id, OLD.design_version_id, OLD.snapshot_id,
                     OLD.kind, OLD.supersedes_id, OLD.created_at) IS DISTINCT FROM
                    (NEW.id, NEW.project_id, NEW.organization_id, NEW.event_id, NEW.design_version_id, NEW.snapshot_id,
                     NEW.kind, NEW.supersedes_id, NEW.created_at) THEN
                    RAISE EXCEPTION 'Actuals identity is immutable' USING ERRCODE = '23514';
                END IF;
            ELSE
                IF jsonb_typeof(NEW.event_ids) <> 'array' OR jsonb_array_length(NEW.event_ids) NOT BETWEEN 1 AND 20
                    OR (NEW.event_id IS NOT NULL AND NEW.event_ids <> jsonb_build_array(NEW.event_id::text))
                    OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(NEW.event_ids) entry
                        WHERE NOT EXISTS (SELECT 1 FROM events e WHERE e.id::text = entry
                                          AND e.project_id = NEW.project_id AND e.organization_id = NEW.organization_id)) THEN
                    RAISE EXCEPTION 'Learning report event selection is outside the tenant' USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER validate_learning_actual BEFORE INSERT OR UPDATE ON learning_actuals
        FOR EACH ROW EXECUTE FUNCTION insho_validate_learning_scope()
    """)
    op.execute("""
        CREATE TRIGGER validate_learning_report BEFORE INSERT ON learning_reports
        FOR EACH ROW EXECUTE FUNCTION insho_validate_learning_scope()
    """)
    op.execute("""
        CREATE TRIGGER preserve_verified_actual BEFORE UPDATE OR DELETE ON learning_actuals
        FOR EACH ROW WHEN (OLD.status = 'verified')
        EXECUTE FUNCTION insho_design_history_immutable('recorded_by_id', 'verified_by_id')
    """)
    op.execute("""
        CREATE TRIGGER preserve_learning_report BEFORE UPDATE OR DELETE ON learning_reports
        FOR EACH ROW EXECUTE FUNCTION insho_design_history_immutable('initiated_by_id', 'ai_execution_id')
    """)
    op.execute("""
        CREATE TRIGGER preserve_learning_disposition BEFORE UPDATE OR DELETE ON learning_dispositions
        FOR EACH ROW EXECUTE FUNCTION insho_design_history_immutable('actor_id')
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_learning_disposition_history', table_name='learning_dispositions')
    op.drop_table('learning_dispositions')
    op.drop_index('ix_learning_report_history', table_name='learning_reports')
    op.drop_table('learning_reports')
    op.drop_index('uq_learning_actual_verified_correction', table_name='learning_actuals', postgresql_where=sa.text("status = 'verified' AND supersedes_id IS NOT NULL"))
    op.drop_index('ix_learning_actual_inputs', table_name='learning_actuals')
    op.drop_index('ix_learning_actual_event_history', table_name='learning_actuals')
    op.drop_table('learning_actuals')
    op.execute("DROP FUNCTION insho_validate_learning_scope()")
