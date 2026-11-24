"""Add the canonical project-scoped spatial scene model.

Revision ID: a14c9d2e7b61
Revises: 93b4c5d6e7f8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a14c9d2e7b61"
down_revision: Union[str, Sequence[str], None] = "93b4c5d6e7f8"
branch_labels = None
depends_on = None

jsonb = postgresql.JSONB(astext_type=sa.Text())
uuid = sa.Uuid()
numeric = sa.Numeric(14, 6)


def upgrade() -> None:
    op.create_table(
        "spatial_scenes",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", uuid, nullable=False),
        sa.Column("event_id", uuid, nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scene_type", sa.String(60), server_default="venue", nullable=False),
        sa.Column("display_unit", sa.String(4), server_default="m", nullable=False),
        sa.Column("coordinate_system", sa.String(40), server_default="right_handed_z_up", nullable=False),
        sa.Column("width_m", numeric, nullable=False),
        sa.Column("depth_m", numeric, nullable=False),
        sa.Column("height_m", numeric, nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("metadata", jsonb, server_default="{}", nullable=False),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_user_id", uuid, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("scene_type <> ''", name="ck_spatial_scenes_type_nonempty"),
        sa.CheckConstraint("display_unit IN ('m', 'cm', 'mm', 'ft', 'in')", name="ck_spatial_scenes_display_unit"),
        sa.CheckConstraint("coordinate_system = 'right_handed_z_up'", name="ck_spatial_scenes_coordinate_system"),
        sa.CheckConstraint("width_m > 0 AND depth_m > 0 AND height_m > 0", name="ck_spatial_scenes_positive_bounds"),
        sa.CheckConstraint("status IN ('draft', 'active', 'archived', 'deleted')", name="ck_spatial_scenes_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_spatial_scenes_id_project"),
    )
    op.create_index("ix_spatial_scenes_project_id", "spatial_scenes", ["project_id"])
    op.create_index("ix_spatial_scenes_event_id", "spatial_scenes", ["event_id"])
    op.create_index("ix_spatial_scenes_created_by_user_id", "spatial_scenes", ["created_by_user_id"])
    op.create_index("ix_spatial_scenes_project_updated", "spatial_scenes", ["project_id", "updated_at"])

    op.create_table(
        "spatial_zones",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scene_id", uuid, nullable=False),
        sa.Column("parent_zone_id", uuid, nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position_x_m", numeric, server_default="0", nullable=False),
        sa.Column("position_y_m", numeric, server_default="0", nullable=False),
        sa.Column("position_z_m", numeric, server_default="0", nullable=False),
        sa.Column("rotation_x_deg", numeric, server_default="0", nullable=False),
        sa.Column("rotation_y_deg", numeric, server_default="0", nullable=False),
        sa.Column("rotation_z_deg", numeric, server_default="0", nullable=False),
        sa.Column("scale_x", numeric, server_default="1", nullable=False),
        sa.Column("scale_y", numeric, server_default="1", nullable=False),
        sa.Column("scale_z", numeric, server_default="1", nullable=False),
        sa.Column("width_m", numeric, nullable=True),
        sa.Column("depth_m", numeric, nullable=True),
        sa.Column("height_m", numeric, nullable=True),
        sa.Column("footprint", jsonb, nullable=True),
        sa.Column("appearance", jsonb, server_default="{}", nullable=False),
        sa.Column("metadata", jsonb, server_default="{}", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("name <> ''", name="ck_spatial_zones_name_nonempty"),
        sa.CheckConstraint("category <> ''", name="ck_spatial_zones_category_nonempty"),
        sa.CheckConstraint("width_m IS NULL OR width_m > 0", name="ck_spatial_zones_width_positive"),
        sa.CheckConstraint("depth_m IS NULL OR depth_m > 0", name="ck_spatial_zones_depth_positive"),
        sa.CheckConstraint("height_m IS NULL OR height_m > 0", name="ck_spatial_zones_height_positive"),
        sa.CheckConstraint("scale_x > 0 AND scale_y > 0 AND scale_z > 0", name="ck_spatial_zones_positive_scale"),
        sa.CheckConstraint("status IN ('active', 'hidden', 'archived')", name="ck_spatial_zones_status"),
        sa.ForeignKeyConstraint(["scene_id"], ["spatial_scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_zone_id", "scene_id"], ["spatial_zones.id", "spatial_zones.scene_id"], ondelete="RESTRICT", name="fk_spatial_zones_parent_same_scene"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "scene_id", name="uq_spatial_zones_id_scene"),
    )
    op.create_index("ix_spatial_zones_scene_id", "spatial_zones", ["scene_id"])
    op.create_index("ix_spatial_zones_parent_zone_id", "spatial_zones", ["parent_zone_id"])
    op.create_index("ix_spatial_zones_scene_parent", "spatial_zones", ["scene_id", "parent_zone_id"])

    op.create_table(
        "spatial_objects",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scene_id", uuid, nullable=False),
        sa.Column("zone_id", uuid, nullable=True),
        sa.Column("parent_object_id", uuid, nullable=True),
        sa.Column("object_type", sa.String(80), server_default="custom", nullable=False),
        sa.Column("category", sa.String(80), server_default="custom", nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("position_x_m", numeric, server_default="0", nullable=False),
        sa.Column("position_y_m", numeric, server_default="0", nullable=False),
        sa.Column("position_z_m", numeric, server_default="0", nullable=False),
        sa.Column("rotation_x_deg", numeric, server_default="0", nullable=False),
        sa.Column("rotation_y_deg", numeric, server_default="0", nullable=False),
        sa.Column("rotation_z_deg", numeric, server_default="0", nullable=False),
        sa.Column("scale_x", numeric, server_default="1", nullable=False),
        sa.Column("scale_y", numeric, server_default="1", nullable=False),
        sa.Column("scale_z", numeric, server_default="1", nullable=False),
        sa.Column("width_m", numeric, nullable=False),
        sa.Column("depth_m", numeric, nullable=False),
        sa.Column("height_m", numeric, nullable=False),
        sa.Column("appearance", jsonb, server_default="{}", nullable=False),
        sa.Column("material_refs", jsonb, server_default="[]", nullable=False),
        sa.Column("properties", jsonb, server_default="{}", nullable=False),
        sa.Column("tags", jsonb, server_default="[]", nullable=False),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("name <> ''", name="ck_spatial_objects_name_nonempty"),
        sa.CheckConstraint("object_type <> '' AND category <> ''", name="ck_spatial_objects_type_category_nonempty"),
        sa.CheckConstraint("width_m > 0 AND depth_m > 0 AND height_m > 0", name="ck_spatial_objects_positive_dimensions"),
        sa.CheckConstraint("scale_x > 0 AND scale_y > 0 AND scale_z > 0", name="ck_spatial_objects_positive_scale"),
        sa.CheckConstraint("status IN ('active', 'hidden', 'archived')", name="ck_spatial_objects_status"),
        sa.ForeignKeyConstraint(["scene_id"], ["spatial_scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id", "scene_id"], ["spatial_zones.id", "spatial_zones.scene_id"], ondelete="RESTRICT", name="fk_spatial_objects_zone_same_scene"),
        sa.ForeignKeyConstraint(["parent_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="RESTRICT", name="fk_spatial_objects_parent_same_scene"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "scene_id", name="uq_spatial_objects_id_scene"),
    )
    op.create_index("ix_spatial_objects_scene_id", "spatial_objects", ["scene_id"])
    op.create_index("ix_spatial_objects_zone_id", "spatial_objects", ["zone_id"])
    op.create_index("ix_spatial_objects_parent_object_id", "spatial_objects", ["parent_object_id"])
    op.create_index("ix_spatial_objects_scene_zone", "spatial_objects", ["scene_id", "zone_id"])
    op.create_index("ix_spatial_objects_scene_parent", "spatial_objects", ["scene_id", "parent_object_id"])

    op.create_table(
        "spatial_object_relationships",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scene_id", uuid, nullable=False),
        sa.Column("source_object_id", uuid, nullable=False),
        sa.Column("target_object_id", uuid, nullable=False),
        sa.Column("relationship_type", sa.String(60), nullable=False),
        sa.Column("metadata", jsonb, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source_object_id <> target_object_id", name="ck_spatial_relationship_no_self_link"),
        sa.CheckConstraint("relationship_type <> ''", name="ck_spatial_relationship_type_nonempty"),
        sa.ForeignKeyConstraint(["scene_id"], ["spatial_scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="CASCADE", name="fk_spatial_relationship_source_same_scene"),
        sa.ForeignKeyConstraint(["target_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="CASCADE", name="fk_spatial_relationship_target_same_scene"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_id", "source_object_id", "target_object_id", "relationship_type", name="uq_spatial_relationship_edge"),
    )
    op.create_index("ix_spatial_relationships_scene", "spatial_object_relationships", ["scene_id"])

    op.create_table(
        "spatial_object_assets",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scene_id", uuid, nullable=False),
        sa.Column("object_id", uuid, nullable=False),
        sa.Column("asset_id", uuid, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("asset_metadata", jsonb, server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('model', 'preview', 'material', 'texture', 'reference')", name="ck_spatial_object_asset_role"),
        sa.ForeignKeyConstraint(["scene_id"], ["spatial_scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="CASCADE", name="fk_spatial_object_assets_object_same_scene"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_id", "asset_id", "role", name="uq_spatial_object_asset_role"),
    )
    op.create_index("ix_spatial_object_assets_scene", "spatial_object_assets", ["scene_id"])
    op.create_index("ix_spatial_object_assets_object_id", "spatial_object_assets", ["object_id"])
    op.create_index("ix_spatial_object_assets_asset_id", "spatial_object_assets", ["asset_id"])

    op.create_table(
        "spatial_scene_revisions",
        sa.Column("id", uuid, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scene_id", uuid, nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("changed_by_user_id", uuid, nullable=True),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", uuid, nullable=False),
        sa.Column("before_state", jsonb, nullable=True),
        sa.Column("after_state", jsonb, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["scene_id"], ["spatial_scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_id", "revision_number", name="uq_spatial_scene_revision_number"),
    )
    op.create_index("ix_spatial_scene_revisions_scene_created", "spatial_scene_revisions", ["scene_id", "created_at"])
    op.create_index("ix_spatial_scene_revisions_changed_by_user_id", "spatial_scene_revisions", ["changed_by_user_id"])


def downgrade() -> None:
    op.drop_table("spatial_scene_revisions")
    op.drop_table("spatial_object_assets")
    op.drop_table("spatial_object_relationships")
    op.drop_table("spatial_objects")
    op.drop_table("spatial_zones")
    op.drop_table("spatial_scenes")
