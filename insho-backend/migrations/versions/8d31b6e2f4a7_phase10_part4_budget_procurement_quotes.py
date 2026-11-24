"""Add project budgets, supplier quotations, and internal procurement history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "8d31b6e2f4a7"
down_revision = "7af04e25b1c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "production_suppliers",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('details', JSONB(), nullable=False),
        sa.Column('last_edited_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('active', 'inactive', 'archived') AND version > 0", name='ck_production_suppliers_state'),
        sa.ForeignKeyConstraint(['last_edited_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_production_suppliers_organization_id'), 'production_suppliers', ['organization_id'], unique=False)
    op.create_table(
        "production_project_suppliers",
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('supplier_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('history', JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('version > 0', name='ck_project_suppliers_version'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_id'], ['production_suppliers.id'], initially='DEFERRED', deferrable=True),
        sa.PrimaryKeyConstraint('project_id', 'supplier_id'),
    )
    op.create_table(
        "production_budgets",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('scope_key', sa.String(length=36), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('currency_precision', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=32, scale=4), nullable=False),
        sa.Column('allocations', JSONB(), nullable=False),
        sa.Column('sources', JSONB(), nullable=False),
        sa.Column('costs', JSONB(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount >= 0 AND amount <> 'NaN'::numeric", name='ck_production_budgets_amount'),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_production_budgets_currency'),
        sa.CheckConstraint("scope_key = coalesce(event_id::text, 'project')", name='ck_production_budgets_scope'),
        sa.CheckConstraint('approved_by_user_id IS NULL OR approved_at IS NOT NULL', name='ck_production_budgets_approval'),
        sa.CheckConstraint('version > 0 AND currency_precision BETWEEN 0 AND 4', name='ck_production_budgets_format'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'scope_key', 'version', name='uq_production_budgets_scope_version'),
    )
    op.create_table(
        "production_supplier_offerings",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('supplier_id', sa.Uuid(), nullable=False),
        sa.Column('material_id', sa.Uuid(), nullable=True),
        sa.Column('definition_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('details', JSONB(), nullable=False),
        sa.Column('last_edited_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("(material_id IS NULL OR kind = 'material') AND (definition_id IS NULL OR kind = 'component')", name='ck_supplier_offerings_reference'),
        sa.CheckConstraint("kind IN ('material', 'component', 'service') AND version > 0", name='ck_supplier_offerings_kind'),
        sa.ForeignKeyConstraint(['definition_id'], ['spatial_object_definitions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['last_edited_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['material_id'], ['production_material_definitions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['supplier_id'], ['production_suppliers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_production_supplier_offerings_supplier_id'), 'production_supplier_offerings', ['supplier_id'], unique=False)
    op.create_table(
        "production_quotations",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('scope_key', sa.String(length=36), nullable=False),
        sa.Column('bom_id', sa.Uuid(), nullable=False),
        sa.Column('supplier_id', sa.Uuid(), nullable=False),
        sa.Column('attachment_id', sa.Uuid(), nullable=True),
        sa.Column('reference', sa.String(length=160), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('quoted_on', sa.Date(), nullable=False),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('currency_precision', sa.Integer(), nullable=False),
        sa.Column('details', JSONB(), nullable=False),
        sa.Column('lines', JSONB(), nullable=False),
        sa.Column('audit', JSONB(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_production_quotations_currency'),
        sa.CheckConstraint("scope_key = coalesce(event_id::text, 'project')", name='ck_production_quotations_scope'),
        sa.CheckConstraint("status IN ('received', 'approved', 'rejected')", name='ck_production_quotations_status'),
        sa.CheckConstraint('valid_until IS NULL OR valid_until >= quoted_on', name='ck_production_quotations_dates'),
        sa.CheckConstraint('version > 0 AND currency_precision BETWEEN 0 AND 4', name='ck_production_quotations_format'),
        sa.ForeignKeyConstraint(['attachment_id'], ['assets.id'], name='fk_production_quotations_attachment', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['bom_id'], ['production_boms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['supplier_id'], ['production_suppliers.id'], initially='DEFERRED', deferrable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'scope_key', 'supplier_id', 'reference', 'version', name='uq_production_quotations_revision'),
    )
    op.create_index(op.f('ix_production_quotations_attachment_id'), 'production_quotations', ['attachment_id'], unique=False)
    op.create_index('ix_production_quotations_bom', 'production_quotations', ['bom_id', 'created_at'], unique=False)
    op.create_table(
        "production_procurements",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=True),
        sa.Column('bom_line_id', sa.Uuid(), nullable=False),
        sa.Column('selected_supplier_id', sa.Uuid(), nullable=True),
        sa.Column('quotation_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('requirement', JSONB(), nullable=False),
        sa.Column('selection', JSONB(), nullable=False),
        sa.Column('approval', JSONB(none_as_null=True), nullable=True),
        sa.Column('audit', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('requested', 'sourcing', 'approved', 'ordered', 'delivered', 'cancelled') AND version > 0", name='ck_production_procurements_state'),
        sa.ForeignKeyConstraint(['bom_line_id'], ['production_bom_lines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['quotation_id'], ['production_quotations.id'], initially='DEFERRED', deferrable=True),
        sa.ForeignKeyConstraint(['selected_supplier_id'], ['production_suppliers.id'], initially='DEFERRED', deferrable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bom_line_id', name='uq_production_procurements_bom_line'),
    )
    op.create_index('ix_production_procurements_scope', 'production_procurements', ['project_id', 'event_id', 'created_at'], unique=False)
    op.create_table(
        "production_value_proposals",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('procurement_id', sa.Uuid(), nullable=False),
        sa.Column('quotation_id', sa.Uuid(), nullable=False),
        sa.Column('material_id', sa.Uuid(), nullable=True),
        sa.Column('definition_id', sa.Uuid(), nullable=True),
        sa.Column('design_artifact_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('proposal', JSONB(), nullable=False),
        sa.Column('audit', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected', 'applied') AND version > 0", name='ck_value_proposals_state'),
        sa.ForeignKeyConstraint(['definition_id'], ['spatial_object_definitions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['design_artifact_id'], ['design_artifacts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['material_id'], ['production_material_definitions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['procurement_id'], ['production_procurements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['quotation_id'], ['production_quotations.id'], initially='DEFERRED', deferrable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_production_value_proposals_procurement_id'), 'production_value_proposals', ['procurement_id'], unique=False)



def downgrade():
    op.drop_table("production_value_proposals")
    op.drop_table("production_procurements")
    op.drop_table("production_quotations")
    op.drop_table("production_supplier_offerings")
    op.drop_table("production_budgets")
    op.drop_table("production_project_suppliers")
    op.drop_table("production_suppliers")
