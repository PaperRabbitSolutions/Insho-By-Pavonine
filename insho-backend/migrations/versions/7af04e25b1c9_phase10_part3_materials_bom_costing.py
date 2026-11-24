"""Add reusable material definitions and revisioned BOM costing."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "7af04e25b1c9"
down_revision = "6c28f1a4d705"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "production_material_definitions",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('current_version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('current_version > 0', name='ck_material_definitions_version'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_production_material_definitions_organization_id'), 'production_material_definitions', ['organization_id'], unique=False)
    op.create_table(
        "production_material_definition_revisions",
        sa.Column('definition_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('category', sa.String(length=80), nullable=False),
        sa.Column('unit', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('unit_rate', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=True),
        sa.Column('allowance_percent', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('details', JSONB(), nullable=False),
        sa.Column('entered_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("(unit_rate IS NULL AND currency IS NULL) OR (unit_rate IS NOT NULL AND unit_rate >= 0 AND unit_rate <> 'NaN'::numeric AND currency ~ '^[A-Z]{3}$' AND currency IS NOT NULL)", name='ck_material_definition_revisions_rate'),
        sa.CheckConstraint("status IN ('active', 'unavailable', 'archived')", name='ck_material_definition_revisions_status'),
        sa.CheckConstraint('allowance_percent BETWEEN 0 AND 1000', name='ck_material_definition_revisions_allowance'),
        sa.CheckConstraint('version > 0', name='ck_material_definition_revisions_version'),
        sa.CheckConstraint(
            "unit IN ('m','cm','mm','ft','in','m2','cm2','mm2','ft2','in2','m3','cm3','mm3','ft3','in3','kg','g','lb','each')",
            name="ck_material_definition_revisions_unit",
        ),
        sa.ForeignKeyConstraint(['definition_id'], ['production_material_definitions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entered_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('definition_id', 'version'),
    )
    op.create_table(
        "production_boms",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('record_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('record_version', sa.Integer(), nullable=False),
        sa.Column('scene_revision', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('currency_precision', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$' AND currency_precision BETWEEN 0 AND 4", name='ck_production_boms_currency'),
        sa.CheckConstraint('approved_by_user_id IS NULL OR approved_at IS NOT NULL', name='ck_production_boms_approval'),
        sa.CheckConstraint('version > 0 AND record_version > 0 AND scene_revision > 0', name='ck_production_boms_versions'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['record_id'], ['production_records.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('record_id', 'version', name='uq_production_boms_record_version'),
    )
    op.create_table(
        "production_bom_lines",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('bom_id', sa.Uuid(), nullable=False),
        sa.Column('line_key', sa.String(length=64), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('origin', sa.String(length=10), nullable=False),
        sa.Column('basis', sa.String(length=10), nullable=False),
        sa.Column('category', sa.String(length=12), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('unit', sa.String(length=8), nullable=True),
        sa.Column('unit_rate', sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column('total', sa.Numeric(precision=32, scale=4), nullable=True),
        sa.Column('inputs', JSONB(), nullable=False),
        sa.Column('cost', JSONB(), nullable=False),
        sa.Column('issues', JSONB(), nullable=False),
        sa.Column('entered_by_user_id', sa.Uuid(), nullable=True),
        sa.CheckConstraint("basis IN ('estimated', 'quoted', 'actual')", name='ck_production_bom_lines_basis'),
        sa.CheckConstraint("category IN ('material', 'component', 'assembly', 'fabrication', 'labour', 'equipment', 'finishing', 'other')", name='ck_production_bom_lines_category'),
        sa.CheckConstraint("origin IN ('generated', 'manual')", name='ck_production_bom_lines_origin'),
        sa.CheckConstraint("quantity IS NULL OR (quantity > 0 AND quantity <> 'NaN'::numeric AND unit IS NOT NULL AND (unit <> 'each' OR quantity = trunc(quantity)))", name='ck_production_bom_lines_quantity'),
        sa.CheckConstraint("total IS NULL OR (total >= 0 AND total <> 'NaN'::numeric)", name='ck_production_bom_lines_total'),
        sa.CheckConstraint("unit IN ('m','cm','mm','ft','in','m2','cm2','mm2','ft2','in2','m3','cm3','mm3','ft3','in3','kg','g','lb','each','h','day')", name="ck_production_bom_lines_unit"),
        sa.CheckConstraint("unit_rate IS NULL OR (unit_rate >= 0 AND unit_rate <> 'NaN'::numeric)", name='ck_production_bom_lines_rate'),
        sa.CheckConstraint('total IS NULL OR (quantity IS NOT NULL AND unit_rate IS NOT NULL AND jsonb_array_length(issues) = 0)', name='ck_production_bom_lines_priced'),
        sa.ForeignKeyConstraint(['bom_id'], ['production_boms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entered_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bom_id', 'line_key', name='uq_production_bom_lines_key'),
    )
    op.create_index('ix_production_bom_lines_bom_position', 'production_bom_lines', ['bom_id', 'position'], unique=False)
    op.add_column('production_materials', sa.Column('definition_id', sa.Uuid(), nullable=True))
    op.add_column('production_materials', sa.Column('version', sa.Integer(), server_default='1', nullable=False))
    op.add_column('production_materials', sa.Column('overrides', JSONB(), server_default='{}', nullable=False))
    op.create_index(op.f('ix_production_materials_definition_id'), 'production_materials', ['definition_id'], unique=False)
    op.create_foreign_key('fk_production_materials_definition', 'production_materials', 'production_material_definitions', ['definition_id'], ['id'], initially='DEFERRED', deferrable=True)


    op.create_check_constraint("ck_production_materials_version", "production_materials", "version > 0")


def downgrade():
    op.drop_constraint("ck_production_materials_version", "production_materials", type_="check")
    op.drop_constraint("fk_production_materials_definition", "production_materials", type_="foreignkey")
    op.drop_index("ix_production_materials_definition_id", table_name="production_materials")
    op.drop_column("production_materials", "overrides")
    op.drop_column("production_materials", "version")
    op.drop_column("production_materials", "definition_id")
    op.drop_table("production_bom_lines")
    op.drop_table("production_boms")
    op.drop_table("production_material_definition_revisions")
    op.drop_table("production_material_definitions")
