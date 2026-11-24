import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    Integer, Numeric, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SpatialScene(Base):
    __tablename__ = "spatial_scenes"
    __table_args__ = (
        CheckConstraint("scene_type <> ''", name="ck_spatial_scenes_type_nonempty"),
        CheckConstraint("display_unit IN ('m', 'cm', 'mm', 'ft', 'in')", name="ck_spatial_scenes_display_unit"),
        CheckConstraint("coordinate_system = 'right_handed_z_up'", name="ck_spatial_scenes_coordinate_system"),
        CheckConstraint("width_m > 0 AND depth_m > 0 AND height_m > 0", name="ck_spatial_scenes_positive_bounds"),
        CheckConstraint("status IN ('draft', 'active', 'archived', 'deleted')", name="ck_spatial_scenes_status"),
        UniqueConstraint("id", "project_id", name="uq_spatial_scenes_id_project"),
        Index("ix_spatial_scenes_project_updated", "project_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scene_type: Mapped[str] = mapped_column(String(60), nullable=False, default="venue", server_default="venue")
    display_unit: Mapped[str] = mapped_column(String(4), nullable=False, default="m", server_default="m")
    coordinate_system: Mapped[str] = mapped_column(String(40), nullable=False, default="right_handed_z_up", server_default="right_handed_z_up")
    width_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    depth_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    height_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SpatialLayer(Base):
    __tablename__ = "spatial_layers"
    __table_args__ = (
        UniqueConstraint("id", "scene_id", name="uq_spatial_layers_id_scene"),
        CheckConstraint("btrim(name) <> ''", name="ck_spatial_layers_name_nonempty"),
        CheckConstraint("order_index >= 0", name="ck_spatial_layers_order"),
        Index("ix_spatial_layers_scene_order", "scene_id", "order_index"),
        Index("uq_spatial_layers_scene_default", "scene_id", unique=True, postgresql_where=text("is_default")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    order_index: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SpatialZone(Base):
    __tablename__ = "spatial_zones"
    __table_args__ = (
        CheckConstraint("name <> ''", name="ck_spatial_zones_name_nonempty"),
        CheckConstraint("category <> ''", name="ck_spatial_zones_category_nonempty"),
        CheckConstraint("width_m IS NULL OR width_m > 0", name="ck_spatial_zones_width_positive"),
        CheckConstraint("depth_m IS NULL OR depth_m > 0", name="ck_spatial_zones_depth_positive"),
        CheckConstraint("height_m IS NULL OR height_m > 0", name="ck_spatial_zones_height_positive"),
        CheckConstraint("scale_x > 0 AND scale_y > 0 AND scale_z > 0", name="ck_spatial_zones_positive_scale"),
        CheckConstraint("status IN ('active', 'hidden', 'archived')", name="ck_spatial_zones_status"),
        UniqueConstraint("id", "scene_id", name="uq_spatial_zones_id_scene"),
        ForeignKeyConstraint(["parent_zone_id", "scene_id"], ["spatial_zones.id", "spatial_zones.scene_id"], ondelete="RESTRICT", name="fk_spatial_zones_parent_same_scene"),
        CheckConstraint("order_index >= 0", name="ck_spatial_zones_order"),
        CheckConstraint("parent_zone_id IS NULL OR id <> parent_zone_id", name="ck_spatial_zones_no_self_parent"),
        Index("ix_spatial_zones_scene_parent", "scene_id", "parent_zone_id", "order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    parent_zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    position_x_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    position_y_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    position_z_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    rotation_x_deg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    rotation_y_deg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    rotation_z_deg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    scale_x: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=1, server_default="1")
    scale_y: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=1, server_default="1")
    scale_z: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=1, server_default="1")
    width_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6), nullable=True)
    depth_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6), nullable=True)
    height_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6), nullable=True)
    footprint: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    appearance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SpatialObject(Base):
    __tablename__ = "spatial_objects"
    __table_args__ = (
        CheckConstraint("name <> ''", name="ck_spatial_objects_name_nonempty"),
        CheckConstraint("object_type <> '' AND category <> ''", name="ck_spatial_objects_type_category_nonempty"),
        CheckConstraint(
            "(node_kind = 'object' AND width_m IS NOT NULL AND depth_m IS NOT NULL AND height_m IS NOT NULL "
            "AND width_m > 0 AND depth_m > 0 AND height_m > 0) OR "
            "(node_kind = 'group' AND width_m IS NULL AND depth_m IS NULL AND height_m IS NULL)",
            name="ck_spatial_objects_kind_dimensions",
        ),
        CheckConstraint("order_index >= 0", name="ck_spatial_objects_order"),
        CheckConstraint("parent_object_id IS NULL OR id <> parent_object_id", name="ck_spatial_objects_no_self_parent"),
        CheckConstraint("scale_x > 0 AND scale_y > 0 AND scale_z > 0", name="ck_spatial_objects_positive_scale"),
        CheckConstraint("status IN ('active', 'hidden', 'archived')", name="ck_spatial_objects_status"),
        CheckConstraint(
            "(library_definition_id IS NULL AND library_definition_version IS NULL) OR "
            "(node_kind = 'object' AND library_definition_id IS NOT NULL "
            "AND library_definition_version IS NOT NULL AND library_definition_version > 0)",
            name="ck_spatial_objects_library_provenance",
        ),
        Index("ix_spatial_objects_library_definition", "library_definition_id"),
        UniqueConstraint("id", "scene_id", name="uq_spatial_objects_id_scene"),
        ForeignKeyConstraint(["parent_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="RESTRICT", name="fk_spatial_objects_parent_same_scene"),
        ForeignKeyConstraint(["zone_id", "scene_id"], ["spatial_zones.id", "spatial_zones.scene_id"], ondelete="RESTRICT", name="fk_spatial_objects_zone_same_scene"),
        ForeignKeyConstraint(["layer_id", "scene_id"], ["spatial_layers.id", "spatial_layers.scene_id"],
                             ondelete="RESTRICT", name="fk_spatial_objects_layer_same_scene"),
        Index("ix_spatial_objects_scene_zone", "scene_id", "zone_id", "order_index"),
        Index("ix_spatial_objects_scene_parent", "scene_id", "parent_object_id", "order_index"),
        CheckConstraint(
            "(layer_id IS NULL AND layer_order_index IS NULL) OR "
            "(layer_id IS NOT NULL AND layer_order_index IS NOT NULL AND layer_order_index >= 0)",
            name="ck_spatial_objects_layer_order",
        ),
        Index("ix_spatial_objects_scene_layer", "scene_id", "layer_id", "layer_order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False, index=True)
    library_definition_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("spatial_object_definitions.id", name="fk_spatial_objects_library_definition",
                   deferrable=True, initially="DEFERRED"), nullable=True,
    )
    library_definition_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    zone_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    layer_order_index: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    layer_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    node_kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default="object")
    order_index: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    parent_object_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False, default="custom", server_default="custom")
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="custom", server_default="custom")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position_x_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    position_y_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    position_z_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    rotation_x_deg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    rotation_y_deg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    rotation_z_deg: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=0, server_default="0")
    scale_x: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=1, server_default="1")
    scale_y: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=1, server_default="1")
    scale_z: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False, default=1, server_default="1")
    width_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6), nullable=True)
    depth_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6), nullable=True)
    height_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 6), nullable=True)
    appearance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    material_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SpatialObjectRelationship(Base):
    __tablename__ = "spatial_object_relationships"
    __table_args__ = (
        CheckConstraint("source_object_id <> target_object_id", name="ck_spatial_relationship_no_self_link"),
        CheckConstraint("relationship_type <> ''", name="ck_spatial_relationship_type_nonempty"),
        UniqueConstraint("scene_id", "source_object_id", "target_object_id", "relationship_type", name="uq_spatial_relationship_edge"),
        ForeignKeyConstraint(["source_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="CASCADE", name="fk_spatial_relationship_source_same_scene"),
        ForeignKeyConstraint(["target_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="CASCADE", name="fk_spatial_relationship_target_same_scene"),
        Index("ix_spatial_relationships_scene", "scene_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False)
    source_object_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_object_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(60), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SpatialObjectAsset(Base):
    __tablename__ = "spatial_object_assets"
    __table_args__ = (
        CheckConstraint("role IN ('model', 'preview', 'material', 'texture', 'reference')", name="ck_spatial_object_asset_role"),
        UniqueConstraint("object_id", "asset_id", "role", name="uq_spatial_object_asset_role"),
        ForeignKeyConstraint(["object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"], ondelete="CASCADE", name="fk_spatial_object_assets_object_same_scene"),
        Index("ix_spatial_object_assets_scene", "scene_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SpatialSceneRevision(Base):
    __tablename__ = "spatial_scene_revisions"
    __table_args__ = (
        UniqueConstraint("scene_id", "revision_number", name="uq_spatial_scene_revision_number"),
        Index("ix_spatial_scene_revisions_scene_created", "scene_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("spatial_scenes.id", ondelete="CASCADE"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    before_state: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
