"""Extend the canonical spatial model with layers, groups, order, and locks."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c481a7d02f64"
down_revision = "a14c9d2e7b61"
branch_labels = None
depends_on = None

KINDS_AND_DIMENSIONS = (
    "(node_kind = 'object' AND width_m IS NOT NULL AND depth_m IS NOT NULL AND height_m IS NOT NULL "
    "AND width_m > 0 AND depth_m > 0 AND height_m > 0) OR "
    "(node_kind = 'group' AND width_m IS NULL AND depth_m IS NULL AND height_m IS NULL)"
)
ORDERED_INDEXES = (
    ("ix_spatial_zones_scene_parent", "spatial_zones", "parent_zone_id"),
    ("ix_spatial_objects_scene_parent", "spatial_objects", "parent_object_id"),
    ("ix_spatial_objects_scene_zone", "spatial_objects", "zone_id"),
)


def upgrade():
    op.create_table(
        "spatial_layers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("scene_id", sa.Uuid(), sa.ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("order_index", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "scene_id", name="uq_spatial_layers_id_scene"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_spatial_layers_name_nonempty"),
        sa.CheckConstraint("order_index >= 0", name="ck_spatial_layers_order"),
    )
    op.create_index("ix_spatial_layers_scene_order", "spatial_layers", ["scene_id", "order_index"])
    for table, parent in (("spatial_zones", "parent_zone_id"), ("spatial_objects", "parent_object_id")):
        op.add_column(table, sa.Column("order_index", sa.BigInteger(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.create_check_constraint(f"ck_{table}_order", table, "order_index >= 0")
        op.create_check_constraint(f"ck_{table}_no_self_parent", table, f"{parent} IS NULL OR id <> {parent}")

    op.add_column("spatial_objects", sa.Column("layer_id", sa.Uuid(), nullable=True))
    op.add_column("spatial_objects", sa.Column("layer_order_index", sa.BigInteger(), nullable=True))
    op.add_column("spatial_objects", sa.Column("node_kind", sa.String(10), nullable=False, server_default="object"))
    op.create_foreign_key("fk_spatial_objects_layer_same_scene", "spatial_objects", "spatial_layers",
                          ["layer_id", "scene_id"], ["id", "scene_id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_spatial_objects_layer_order", "spatial_objects",
                               "(layer_id IS NULL AND layer_order_index IS NULL) OR "
                               "(layer_id IS NOT NULL AND layer_order_index IS NOT NULL AND layer_order_index >= 0)")
    op.create_index("ix_spatial_objects_scene_layer", "spatial_objects", ["scene_id", "layer_id", "layer_order_index"])
    op.drop_constraint("ck_spatial_objects_positive_dimensions", "spatial_objects", type_="check")
    for column in ("width_m", "depth_m", "height_m"):
        op.alter_column("spatial_objects", column, existing_type=sa.Numeric(14, 6), nullable=True)
    op.create_check_constraint("ck_spatial_objects_kind_dimensions", "spatial_objects", KINDS_AND_DIMENSIONS)

    for name, table, parent in ORDERED_INDEXES:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, ["scene_id", parent, "order_index"])


def downgrade():
    # A transformed group cannot be converted to a measured Part 1 object safely.
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM spatial_layers) OR
             EXISTS (SELECT 1 FROM spatial_objects WHERE node_kind = 'group' OR is_locked OR order_index > 0) OR
             EXISTS (SELECT 1 FROM spatial_zones WHERE is_locked OR order_index > 0) THEN
            RAISE EXCEPTION 'Downgrade would discard scene graph data';
          END IF;
        END $$;
    """)
    for name, table, parent in ORDERED_INDEXES:
        op.drop_index(name, table_name=table)
        op.create_index(name, table, ["scene_id", parent])
    op.drop_constraint("ck_spatial_objects_kind_dimensions", "spatial_objects", type_="check")
    for column in ("width_m", "depth_m", "height_m"):
        op.alter_column("spatial_objects", column, existing_type=sa.Numeric(14, 6), nullable=False)
    op.create_check_constraint("ck_spatial_objects_positive_dimensions", "spatial_objects",
                               "width_m > 0 AND depth_m > 0 AND height_m > 0")
    op.drop_index("ix_spatial_objects_scene_layer", table_name="spatial_objects")
    op.drop_constraint("fk_spatial_objects_layer_same_scene", "spatial_objects", type_="foreignkey")
    op.drop_column("spatial_objects", "node_kind")
    op.drop_constraint("ck_spatial_objects_layer_order", "spatial_objects", type_="check")
    op.drop_column("spatial_objects", "layer_order_index")
    op.drop_column("spatial_objects", "layer_id")
    for table in ("spatial_objects", "spatial_zones"):
        op.drop_constraint(f"ck_{table}_no_self_parent", table, type_="check")
        op.drop_constraint(f"ck_{table}_order", table, type_="check")
        op.drop_column(table, "is_locked")
        op.drop_column(table, "order_index")
    op.drop_table("spatial_layers")
