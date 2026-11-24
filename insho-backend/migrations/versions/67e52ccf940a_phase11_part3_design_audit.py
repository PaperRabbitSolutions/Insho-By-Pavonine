"""phase11 part3 design audit

Revision ID: 67e52ccf940a
Revises: 921d7c306abe
Create Date: 2026-11-24 11:57:33.195350

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '67e52ccf940a'
down_revision = '921d7c306abe'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('design_audits',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('design_version_id', sa.Uuid(), nullable=False),
        sa.Column('snapshot_id', sa.Uuid(), nullable=False),
        sa.Column('initiated_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('request_id', sa.Uuid(), nullable=False),
        sa.Column('retry_of_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=28), nullable=False),
        sa.Column('configuration_version', sa.String(length=24), nullable=False),
        sa.Column('options', postgresql.JSONB(), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('results', postgresql.JSONB(), nullable=False),
        sa.Column('summary', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column('warnings', postgresql.JSONB(), nullable=False),
        sa.Column('retryable', sa.Boolean(), nullable=False),
        sa.Column('evidence_sources', postgresql.JSONB(), nullable=False),
        sa.Column('ai_execution_id', sa.Uuid(), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('model', sa.String(length=150), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("(status = 'running' AND completed_at IS NULL) OR (status IN ('completed','completed_with_warnings','failed','cancelled') AND completed_at IS NOT NULL)", name='ck_design_audits_status'),
        sa.CheckConstraint("CASE WHEN status IN ('completed','completed_with_warnings') THEN summary IS NOT NULL AND jsonb_array_length(results) = 18 ELSE summary IS NULL AND jsonb_array_length(results) = 0 END", name='ck_design_audits_results'),
        sa.ForeignKeyConstraint(['ai_execution_id'], ['ai_executions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['design_version_id', 'project_id'], ['design_versions.id', 'design_versions.project_id'], name='fk_design_audits_version_project', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['event_id', 'project_id'], ['events.id', 'events.project_id'], name='fk_design_audits_event_project', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['initiated_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['retry_of_id', 'design_version_id'], ['design_audits.id', 'design_audits.design_version_id'], name='fk_design_audits_retry_version'),
        sa.ForeignKeyConstraint(['snapshot_id'], ['design_snapshots.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'design_version_id', name='uq_design_audits_id_version')
    )
    op.create_index('ix_design_audits_project_created', 'design_audits', ['project_id', 'started_at', 'id'], unique=False)
    op.create_index('ix_design_audits_version_created', 'design_audits', ['design_version_id', 'started_at', 'id'], unique=False)
    op.execute("""
        CREATE FUNCTION insho_validate_design_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM design_versions v JOIN projects p ON p.id = v.project_id
                    WHERE v.id = NEW.design_version_id AND p.id = NEW.project_id
                    AND p.organization_id = NEW.organization_id AND v.snapshot_id = NEW.snapshot_id
                    AND v.event_id IS NOT DISTINCT FROM NEW.event_id
                ) THEN
                    RAISE EXCEPTION 'The audit does not match the design version scope' USING ERRCODE = '23514';
                END IF;
            ELSIF (NEW.id, NEW.organization_id, NEW.project_id, NEW.event_id, NEW.design_version_id, NEW.snapshot_id,
                   NEW.configuration_version, NEW.options, NEW.input_fingerprint, NEW.request_id, NEW.retry_of_id, NEW.started_at)
                   IS DISTINCT FROM
                  (OLD.id, OLD.organization_id, OLD.project_id, OLD.event_id, OLD.design_version_id, OLD.snapshot_id,
                   OLD.configuration_version, OLD.options, OLD.input_fingerprint, OLD.request_id, OLD.retry_of_id, OLD.started_at) THEN
                RAISE EXCEPTION 'Audit input identity is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER validate_design_audit BEFORE INSERT OR UPDATE ON design_audits
        FOR EACH ROW EXECUTE FUNCTION insho_validate_design_audit()
    """)
    op.execute("""
        CREATE TRIGGER protect_final_design_audit BEFORE UPDATE OR DELETE ON design_audits
        FOR EACH ROW WHEN (OLD.status <> 'running')
        EXECUTE FUNCTION insho_design_history_immutable('initiated_by_user_id', 'ai_execution_id')
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER protect_final_design_audit ON design_audits")
    op.execute("DROP TRIGGER validate_design_audit ON design_audits")
    op.execute("DROP FUNCTION insho_validate_design_audit()")
    op.drop_index('ix_design_audits_version_created', table_name='design_audits')
    op.drop_index('ix_design_audits_project_created', table_name='design_audits')
    op.drop_table('design_audits')
