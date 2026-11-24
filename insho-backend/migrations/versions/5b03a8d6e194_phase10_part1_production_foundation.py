"""Add scene-scoped production and measurement provenance."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "5b03a8d6e194"
down_revision = "02e73da81bf4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "production_records",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('scene_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='draft', nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('scene_revision', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("(status = 'draft' AND approved_at IS NULL) OR (status = 'approved' AND approved_at IS NOT NULL)", name='ck_production_records_status'),
        sa.CheckConstraint('version > 0 AND scene_revision > 0', name='ck_production_records_versions'),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['scene_id'], ['spatial_scenes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'scene_id', name='uq_production_records_id_scene'),
    )
    op.create_index('ix_production_records_scene_created', 'production_records', ['scene_id', 'created_at', 'id'], unique=False)
    op.create_table(
        "production_materials",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('record_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['record_id'], ['production_records.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'record_id', name='uq_production_materials_id_record'),
    )
    op.create_index(op.f('ix_production_materials_record_id'), 'production_materials', ['record_id'], unique=False)
    op.create_table(
        "production_items",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('record_id', sa.Uuid(), nullable=False),
        sa.Column('scene_id', sa.Uuid(), nullable=False),
        sa.Column('parent_item_id', sa.Uuid(), nullable=True),
        sa.Column('material_id', sa.Uuid(), nullable=True),
        sa.Column('spatial_object_id', sa.Uuid(), nullable=True),
        sa.Column('spatial_zone_id', sa.Uuid(), nullable=True),
        sa.Column('library_definition_id', sa.Uuid(), nullable=True),
        sa.Column('reviewed_library_version', sa.Integer(), nullable=True),
        sa.Column('source', JSONB(), server_default='{}', nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('assembly', 'component')", name='ck_production_items_kind'),
        sa.CheckConstraint('id <> parent_item_id', name='ck_production_items_parent'),
        sa.CheckConstraint('reviewed_library_version IS NULL OR reviewed_library_version > 0', name='ck_production_items_library_version'),
        sa.ForeignKeyConstraint(['library_definition_id'], ['spatial_object_definitions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['material_id', 'record_id'], ['production_materials.id', 'production_materials.record_id'], name='fk_production_items_material_record', initially='DEFERRED', deferrable=True),
        sa.ForeignKeyConstraint(['parent_item_id', 'record_id'], ['production_items.id', 'production_items.record_id'], name='fk_production_items_parent_record', initially='DEFERRED', deferrable=True),
        sa.ForeignKeyConstraint(['record_id', 'scene_id'], ['production_records.id', 'production_records.scene_id'], name='fk_production_items_record_scene', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['spatial_object_id', 'scene_id'], ['spatial_objects.id', 'spatial_objects.scene_id'], name='fk_production_items_object_scene', initially='DEFERRED', deferrable=True),
        sa.ForeignKeyConstraint(['spatial_object_id'], ['spatial_objects.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['spatial_zone_id', 'scene_id'], ['spatial_zones.id', 'spatial_zones.scene_id'], name='fk_production_items_zone_scene', initially='DEFERRED', deferrable=True),
        sa.ForeignKeyConstraint(['spatial_zone_id'], ['spatial_zones.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'record_id', name='uq_production_items_id_record'),
        sa.UniqueConstraint('id', 'scene_id', name='uq_production_items_id_scene'),
    )
    op.create_index(op.f('ix_production_items_library_definition_id'), 'production_items', ['library_definition_id'], unique=False)
    op.create_index(op.f('ix_production_items_record_id'), 'production_items', ['record_id'], unique=False)
    op.create_index(op.f('ix_production_items_spatial_object_id'), 'production_items', ['spatial_object_id'], unique=False)
    op.create_index(op.f('ix_production_items_spatial_zone_id'), 'production_items', ['spatial_zone_id'], unique=False)
    op.create_table(
        "production_specifications",
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('item_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        sa.CheckConstraint('version > 0', name='ck_production_specs_version'),
        sa.ForeignKeyConstraint(['item_id'], ['production_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'item_id', name='uq_production_specs_id_item'),
    )
    op.create_index(op.f('ix_production_specifications_item_id'), 'production_specifications', ['item_id'], unique=False)
    op.create_table(
        "production_measurements",
        sa.Column('item_id', sa.Uuid(), nullable=False),
        sa.Column('scene_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('value', sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column('specification_id', sa.Uuid(), nullable=True),
        sa.Column('recommendation_id', sa.Uuid(), nullable=True),
        sa.Column('source_type', sa.String(length=24), nullable=False),
        sa.Column('source', JSONB(), nullable=False),
        sa.Column('entered_by_user_id', sa.Uuid(), nullable=True),
        sa.CheckConstraint("kind IN ('length', 'width', 'height', 'area', 'volume', 'count', 'weight')", name='ck_production_measurements_kind'),
        sa.CheckConstraint("source_type IN ('spatial_geometry', 'object_library', 'user_entered', 'ai_suggestion', 'technical_specification') AND ((source_type = 'technical_specification') = (specification_id IS NOT NULL)) AND ((source_type = 'ai_suggestion') = (recommendation_id IS NOT NULL)) AND jsonb_typeof(source) = 'object'", name='ck_production_measurements_source'),
        sa.CheckConstraint("value >= 0 AND value <> 'NaN'::numeric AND (kind = 'count' OR value > 0) AND (kind <> 'count' OR value = trunc(value))", name='ck_production_measurements_value'),
        sa.ForeignKeyConstraint(['entered_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['item_id', 'scene_id'], ['production_items.id', 'production_items.scene_id'], name='fk_production_measurements_item_scene', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['scene_id', 'recommendation_id'], ['spatial_design_reviews.scene_id', 'spatial_design_reviews.recommendation_id'], name='fk_production_measurements_recommendation_scene', initially='DEFERRED', deferrable=True),
        sa.ForeignKeyConstraint(['specification_id', 'item_id'], ['production_specifications.id', 'production_specifications.item_id'], name='fk_production_measurements_spec_item', initially='DEFERRED', deferrable=True),
        sa.PrimaryKeyConstraint('item_id', 'kind'),
    )



def downgrade() -> None:
    op.drop_table("production_measurements")
    op.drop_table("production_specifications")
    op.drop_table("production_items")
    op.drop_table("production_materials")
    op.drop_table("production_records")
