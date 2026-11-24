"""Reusable spatial definitions linked to existing Phase 6 assets."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ObjectDefinition(Base):
    __tablename__ = "spatial_object_definitions"
    __table_args__ = (
        CheckConstraint("btrim(name) <> '' AND btrim(category) <> '' AND btrim(object_type) <> ''",
                        name="ck_spatial_definitions_names"),
        CheckConstraint("width_m > 0 AND depth_m > 0 AND height_m > 0", name="ck_spatial_definitions_dimensions"),
        CheckConstraint("version > 0", name="ck_spatial_definitions_version"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_spatial_definitions_status"),
        CheckConstraint("project_id IS NULL OR workspace_id IS NOT NULL", name="ck_spatial_definitions_project_scope"),
        Index("ix_spatial_definitions_catalog", "organization_id", "status", "category", "id"),
        Index("ix_spatial_definitions_scope", "organization_id", "workspace_id", "project_id"),
        Index("ix_spatial_definitions_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4,
                                          server_default=text("gen_random_uuid()"))
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(80), nullable=False)
    tags: Mapped[list] = mapped_column(ARRAY(String(80)), nullable=False, default=list, server_default="{}")
    width_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    depth_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    height_m: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    default_transform: Mapped[dict] = mapped_column(JSONB, nullable=False)
    geometry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan_compatible: Mapped[bool] = mapped_column(nullable=False)
    appearance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    material_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ObjectDefinitionAsset(Base):
    __tablename__ = "spatial_object_definition_assets"
    __table_args__ = (
        CheckConstraint("role IN ('model', 'preview', 'material', 'texture', 'reference')",
                        name="ck_spatial_definition_assets_role"),
        Index("ix_spatial_definition_assets_asset", "asset_id"),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spatial_object_definitions.id", ondelete="CASCADE"), primary_key=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
