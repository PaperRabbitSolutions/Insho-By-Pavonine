"""Tenant material revisions and immutable production cost snapshots."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MaterialDefinition(Base):
    __tablename__ = "production_material_definitions"
    __table_args__ = (
        CheckConstraint("current_version > 0", name="ck_material_definitions_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    current_version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MaterialDefinitionRevision(Base):
    __tablename__ = "production_material_definition_revisions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_material_definition_revisions_version"),
        CheckConstraint(
            "unit IN ('m','cm','mm','ft','in','m2','cm2','mm2','ft2','in2','m3','cm3','mm3','ft3','in3','kg','g','lb','each')",
            name="ck_material_definition_revisions_unit",
        ),
        CheckConstraint("status IN ('active', 'unavailable', 'archived')", name="ck_material_definition_revisions_status"),
        CheckConstraint(
            "(unit_rate IS NULL AND currency IS NULL) OR "
            "(unit_rate IS NOT NULL AND unit_rate >= 0 AND unit_rate <> 'NaN'::numeric "
            "AND currency ~ '^[A-Z]{3}$' AND currency IS NOT NULL)",
            name="ck_material_definition_revisions_rate",
        ),
        CheckConstraint("allowance_percent BETWEEN 0 AND 1000", name="ck_material_definition_revisions_allowance"),
    )

    definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("production_material_definitions.id", ondelete="CASCADE"), primary_key=True,
    )
    version: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12))
    unit_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6))
    currency: Mapped[Optional[str]] = mapped_column(String(3))
    allowance_percent: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    details: Mapped[dict] = mapped_column(JSONB)
    entered_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionBOM(Base):
    __tablename__ = "production_boms"
    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_production_boms_record_version"),
        CheckConstraint("version > 0 AND record_version > 0 AND scene_revision > 0", name="ck_production_boms_versions"),
        CheckConstraint("currency ~ '^[A-Z]{3}$' AND currency_precision BETWEEN 0 AND 4", name="ck_production_boms_currency"),
        CheckConstraint("approved_by_user_id IS NULL OR approved_at IS NOT NULL", name="ck_production_boms_approval"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    record_id: Mapped[UUID] = mapped_column(ForeignKey("production_records.id", ondelete="CASCADE"))
    version: Mapped[int]
    record_version: Mapped[int]
    scene_revision: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    currency_precision: Mapped[int]
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProductionBOMLine(Base):
    __tablename__ = "production_bom_lines"
    __table_args__ = (
        UniqueConstraint("bom_id", "line_key", name="uq_production_bom_lines_key"),
        CheckConstraint("origin IN ('generated', 'manual')", name="ck_production_bom_lines_origin"),
        CheckConstraint("basis IN ('estimated', 'quoted', 'actual')", name="ck_production_bom_lines_basis"),
        CheckConstraint(
            "category IN ('material', 'component', 'assembly', 'fabrication', 'labour', 'equipment', 'finishing', 'other')",
            name="ck_production_bom_lines_category",
        ),
        CheckConstraint(
            "quantity IS NULL OR (quantity > 0 AND quantity <> 'NaN'::numeric AND unit IS NOT NULL "
            "AND (unit <> 'each' OR quantity = trunc(quantity)))",
            name="ck_production_bom_lines_quantity",
        ),
        CheckConstraint(
            "unit IN ('m','cm','mm','ft','in','m2','cm2','mm2','ft2','in2','m3','cm3','mm3','ft3','in3','kg','g','lb','each','h','day')",
            name="ck_production_bom_lines_unit",
        ),
        CheckConstraint("unit_rate IS NULL OR (unit_rate >= 0 AND unit_rate <> 'NaN'::numeric)", name="ck_production_bom_lines_rate"),
        CheckConstraint("total IS NULL OR (total >= 0 AND total <> 'NaN'::numeric)", name="ck_production_bom_lines_total"),
        CheckConstraint(
            "total IS NULL OR (quantity IS NOT NULL AND unit_rate IS NOT NULL AND jsonb_array_length(issues) = 0)",
            name="ck_production_bom_lines_priced",
        ),
        Index("ix_production_bom_lines_bom_position", "bom_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    bom_id: Mapped[UUID] = mapped_column(ForeignKey("production_boms.id", ondelete="CASCADE"))
    line_key: Mapped[str] = mapped_column(String(64))
    position: Mapped[int]
    name: Mapped[str] = mapped_column(String(160))
    origin: Mapped[str] = mapped_column(String(10))
    basis: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(12))
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6))
    unit: Mapped[Optional[str]] = mapped_column(String(8))
    unit_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 6))
    total: Mapped[Optional[Decimal]] = mapped_column(Numeric(32, 4))
    inputs: Mapped[dict] = mapped_column(JSONB)
    cost: Mapped[dict] = mapped_column(JSONB)
    issues: Mapped[list] = mapped_column(JSONB)
    entered_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
