"""Persist scoped object definitions and pin provenance on scene objects."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB


revision = "eb4c51ad9027"
down_revision = "d37b196ac241"
branch_labels = None
depends_on = None

PROVENANCE = (
    "(library_definition_id IS NULL AND library_definition_version IS NULL) OR "
    "(node_kind = 'object' AND library_definition_id IS NOT NULL "
    "AND library_definition_version IS NOT NULL AND library_definition_version > 0)"
)


def upgrade():
    op.create_table(
        "spatial_object_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE")),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("object_type", sa.String(80), nullable=False),
        sa.Column("tags", ARRAY(sa.String(80)), nullable=False, server_default="{}"),
        *(sa.Column(field, sa.Numeric(14, 6), nullable=False) for field in ("width_m", "depth_m", "height_m")),
        sa.Column("default_transform", JSONB(), nullable=False),
        sa.Column("geometry", JSONB(), nullable=False),
        sa.Column("plan_compatible", sa.Boolean(), nullable=False),
        sa.Column("appearance", JSONB(), nullable=False, server_default="{}"),
        sa.Column("material_refs", JSONB(), nullable=False, server_default="[]"),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("btrim(name) <> '' AND btrim(category) <> '' AND btrim(object_type) <> ''",
                           name="ck_spatial_definitions_names"),
        sa.CheckConstraint("width_m > 0 AND depth_m > 0 AND height_m > 0", name="ck_spatial_definitions_dimensions"),
        sa.CheckConstraint("version > 0", name="ck_spatial_definitions_version"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_spatial_definitions_status"),
        sa.CheckConstraint("project_id IS NULL OR workspace_id IS NOT NULL", name="ck_spatial_definitions_project_scope"),
    )
    op.create_index("ix_spatial_definitions_catalog", "spatial_object_definitions", ["organization_id", "status", "category", "id"])
    op.create_index("ix_spatial_definitions_scope", "spatial_object_definitions", ["organization_id", "workspace_id", "project_id"])
    op.create_index("ix_spatial_definitions_tags", "spatial_object_definitions", ["tags"], postgresql_using="gin")
    op.create_table(
        "spatial_object_definition_assets",
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("spatial_object_definitions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("role", sa.String(20), primary_key=True),
        sa.CheckConstraint("role IN ('model', 'preview', 'material', 'texture', 'reference')",
                           name="ck_spatial_definition_assets_role"),
    )
    op.create_index("ix_spatial_definition_assets_asset", "spatial_object_definition_assets", ["asset_id"])
    op.add_column("spatial_objects", sa.Column("library_definition_id", sa.Uuid()))
    op.add_column("spatial_objects", sa.Column("library_definition_version", sa.Integer()))
    op.create_foreign_key("fk_spatial_objects_library_definition", "spatial_objects", "spatial_object_definitions",
                          ["library_definition_id"], ["id"], deferrable=True, initially="DEFERRED")
    op.create_check_constraint("ck_spatial_objects_library_provenance", "spatial_objects", PROVENANCE)
    op.create_index("ix_spatial_objects_library_definition", "spatial_objects", ["library_definition_id"])


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM spatial_object_definitions)
               OR EXISTS (SELECT 1 FROM spatial_objects WHERE library_definition_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Downgrade would discard object catalog data';
            END IF;
        END $$;
    """)
    op.drop_index("ix_spatial_objects_library_definition", table_name="spatial_objects")
    op.drop_constraint("ck_spatial_objects_library_provenance", "spatial_objects", type_="check")
    op.drop_constraint("fk_spatial_objects_library_definition", "spatial_objects", type_="foreignkey")
    op.drop_column("spatial_objects", "library_definition_version")
    op.drop_column("spatial_objects", "library_definition_id")
    op.drop_table("spatial_object_definition_assets")
    op.drop_table("spatial_object_definitions")
