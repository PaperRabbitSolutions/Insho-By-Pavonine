"""Production records and their scene-scoped work breakdown."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    Numeric, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductionRecord(Base):
    __tablename__ = "production_records"
    __table_args__ = (
        UniqueConstraint("id", "scene_id", name="uq_production_records_id_scene"),
        ForeignKeyConstraint(
            ["supersedes_id", "scene_id"], ["production_records.id", "production_records.scene_id"],
            name="fk_production_records_predecessor_scene",
        ),
        CheckConstraint("supersedes_id <> id", name="ck_production_records_predecessor"),
        Index("ix_production_records_supersedes_id", "supersedes_id"),
        CheckConstraint("version > 0 AND scene_revision > 0", name="ck_production_records_versions"),
        CheckConstraint(
            "(status = 'draft' AND approved_at IS NULL) OR (status = 'approved' AND approved_at IS NOT NULL)",
            name="ck_production_records_status",
        ),
        Index("ix_production_records_scene_created", "scene_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    scene_id: Mapped[UUID] = mapped_column(ForeignKey("spatial_scenes.id", ondelete="CASCADE"))
    supersedes_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    scene_revision: Mapped[int]
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductionWorkflowRevision(Base):
    __tablename__ = "production_workflow_revisions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_production_workflow_version"),
        CheckConstraint(
            "status IN ('draft', 'design_approved', 'technical_review', 'production_ready', 'in_fabrication', "
            "'on_site', 'installed', 'inspected', 'live', 'dismantling', 'archived')",
            name="ck_production_workflow_status",
        ),
        CheckConstraint("jsonb_typeof(tasks) = 'array'", name="ck_production_workflow_tasks"),
    )

    record_id: Mapped[UUID] = mapped_column(ForeignKey("production_records.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    tasks: Mapped[list] = mapped_column(JSONB)
    readiness_basis: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    evidence_reference: Mapped[Optional[str]] = mapped_column(Text)
    changed_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionMaterial(Base):
    __tablename__ = "production_materials"
    __table_args__ = (
        UniqueConstraint("id", "record_id", name="uq_production_materials_id_record"),
        CheckConstraint("version > 0", name="ck_production_materials_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    record_id: Mapped[UUID] = mapped_column(ForeignKey("production_records.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[Optional[str]] = mapped_column(Text)
    definition_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey(
        "production_material_definitions.id", name="fk_production_materials_definition", deferrable=True, initially="DEFERRED",
    ), index=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class ProductionItem(Base):
    __tablename__ = "production_items"
    __table_args__ = (
        UniqueConstraint("id", "record_id", name="uq_production_items_id_record"),
        UniqueConstraint("id", "scene_id", name="uq_production_items_id_scene"),
        ForeignKeyConstraint(
            ["record_id", "scene_id"], ["production_records.id", "production_records.scene_id"],
            ondelete="CASCADE", name="fk_production_items_record_scene",
        ),
        ForeignKeyConstraint(
            ["parent_item_id", "record_id"], ["production_items.id", "production_items.record_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_items_parent_record",
        ),
        ForeignKeyConstraint(
            ["material_id", "record_id"], ["production_materials.id", "production_materials.record_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_items_material_record",
        ),
        # Live references clear when Phase 9 deletes a source. The source snapshot retains its ID.
        ForeignKeyConstraint(
            ["spatial_object_id", "scene_id"], ["spatial_objects.id", "spatial_objects.scene_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_items_object_scene",
        ),
        ForeignKeyConstraint(
            ["spatial_zone_id", "scene_id"], ["spatial_zones.id", "spatial_zones.scene_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_items_zone_scene",
        ),
        CheckConstraint("kind IN ('assembly', 'component')", name="ck_production_items_kind"),
        CheckConstraint("id <> parent_item_id", name="ck_production_items_parent"),
        CheckConstraint(
            "reviewed_library_version IS NULL OR reviewed_library_version > 0",
            name="ck_production_items_library_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    record_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    scene_id: Mapped[UUID] = mapped_column(Uuid)
    parent_item_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    material_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    spatial_object_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("spatial_objects.id", ondelete="SET NULL"), index=True)
    spatial_zone_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("spatial_zones.id", ondelete="SET NULL"), index=True)
    library_definition_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("spatial_object_definitions.id", ondelete="SET NULL"), index=True,
    )
    reviewed_library_version: Mapped[Optional[int]]
    source: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[Optional[str]] = mapped_column(Text)


class ProductionSpecification(Base):
    __tablename__ = "production_specifications"
    __table_args__ = (
        UniqueConstraint("id", "item_id", name="uq_production_specs_id_item"),
        UniqueConstraint("supersedes_id", name="uq_production_specs_supersedes"),
        ForeignKeyConstraint(
            ["supersedes_id", "item_id"], ["production_specifications.id", "production_specifications.item_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_specs_predecessor_item",
        ),
        CheckConstraint("version > 0", name="ck_production_specs_version"),
        CheckConstraint("supersedes_id <> id", name="ck_production_specs_predecessor"),
        CheckConstraint("technical IS NULL OR jsonb_typeof(technical) = 'object'", name="ck_production_specs_technical"),
        CheckConstraint("approved_by_user_id IS NULL OR approved_at IS NOT NULL", name="ck_production_specs_approval"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    item_id: Mapped[UUID] = mapped_column(ForeignKey("production_items.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    technical: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    supersedes_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey(
        "users.id", ondelete="SET NULL", name="fk_production_specs_approved_by",
    ))


class ProductionQuantity(Base):
    __tablename__ = "production_quantities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "record_id"], ["production_items.id", "production_items.record_id"],
            ondelete="CASCADE", name="fk_production_quantities_item_record",
        ),
        UniqueConstraint(
            "record_id", "target", "target_id", "method", "variant", "version",
            name="uq_production_quantities_source_version",
        ),
        CheckConstraint("version > 0 AND engine_version > 0 AND scene_revision > 0", name="ck_production_quantities_versions"),
        CheckConstraint("target IN ('scene', 'object', 'zone')", name="ck_production_quantities_target"),
        CheckConstraint(
            "(method IN ('plan_area', 'surface_area', 'led_surface') AND unit = 'm2') OR "
            "(method = 'linear' AND unit = 'm') OR (method = 'solid_volume' AND unit = 'm3') OR "
            "(method IN ('object_count', 'floral_stems') AND unit = 'each')",
            name="ck_production_quantities_method_unit",
        ),
        CheckConstraint(
            "value IS NULL OR (value > 0 AND value <> 'NaN'::numeric AND (unit <> 'each' OR value = trunc(value)))",
            name="ck_production_quantities_value",
        ),
        CheckConstraint(
            "jsonb_typeof(errors) = 'array' AND ((status = 'calculated' AND value IS NOT NULL AND jsonb_array_length(errors) = 0) "
            "OR (status = 'invalid' AND value IS NULL AND jsonb_array_length(errors) > 0))",
            name="ck_production_quantities_status",
        ),
        Index("ix_production_quantities_record_created", "record_id", "calculated_at", "id"),
        Index("ix_production_quantities_item", "item_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    record_id: Mapped[UUID] = mapped_column(Uuid)
    item_id: Mapped[UUID] = mapped_column(Uuid)
    target: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[UUID] = mapped_column(Uuid)
    method: Mapped[str] = mapped_column(String(20))
    variant: Mapped[str] = mapped_column(String(4), server_default="")
    version: Mapped[int]
    engine_version: Mapped[int]
    scene_revision: Mapped[int]
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSONB)
    formula: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSONB)
    inputs: Mapped[dict] = mapped_column(JSONB)
    outputs: Mapped[dict] = mapped_column(JSONB)
    value: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6))
    unit: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(12))
    errors: Mapped[list] = mapped_column(JSONB)
    calculated_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionMeasurement(Base):
    __tablename__ = "production_measurements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["item_id", "scene_id"], ["production_items.id", "production_items.scene_id"],
            ondelete="CASCADE", name="fk_production_measurements_item_scene",
        ),
        ForeignKeyConstraint(
            ["scene_id", "recommendation_id"], ["spatial_design_reviews.scene_id", "spatial_design_reviews.recommendation_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_measurements_recommendation_scene",
        ),
        ForeignKeyConstraint(
            ["specification_id", "item_id"], ["production_specifications.id", "production_specifications.item_id"],
            deferrable=True, initially="DEFERRED", name="fk_production_measurements_spec_item",
        ),
        CheckConstraint(
            "kind IN ('length', 'width', 'height', 'area', 'volume', 'count', 'weight')",
            name="ck_production_measurements_kind",
        ),
        CheckConstraint(
            "value >= 0 AND value <> 'NaN'::numeric AND (kind = 'count' OR value > 0) "
            "AND (kind <> 'count' OR value = trunc(value))",
            name="ck_production_measurements_value",
        ),
        CheckConstraint(
            "source_type IN ('spatial_geometry', 'object_library', 'user_entered', 'ai_suggestion', 'technical_specification') "
            "AND ((source_type = 'technical_specification') = (specification_id IS NOT NULL)) "
            "AND ((source_type = 'ai_suggestion') = (recommendation_id IS NOT NULL)) "
            "AND jsonb_typeof(source) = 'object'",
            name="ck_production_measurements_source",
        ),
    )

    item_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    scene_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    specification_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    recommendation_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    source_type: Mapped[str] = mapped_column(String(24))
    source: Mapped[dict] = mapped_column(JSONB)
    entered_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
