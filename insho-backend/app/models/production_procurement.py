"""Project budgets, supplier sourcing, and internal procurement decisions."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductionBudget(Base):
    __tablename__ = "production_budgets"
    __table_args__ = (
        UniqueConstraint("project_id", "scope_key", "version", name="uq_production_budgets_scope_version"),
        CheckConstraint("version > 0 AND currency_precision BETWEEN 0 AND 4", name="ck_production_budgets_format"),
        CheckConstraint("amount >= 0 AND amount <> 'NaN'::numeric", name="ck_production_budgets_amount"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_production_budgets_currency"),
        CheckConstraint("scope_key = coalesce(event_id::text, 'project')", name="ck_production_budgets_scope"),
        CheckConstraint("approved_by_user_id IS NULL OR approved_at IS NOT NULL", name="ck_production_budgets_approval"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    event_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    scope_key: Mapped[str] = mapped_column(String(36))
    version: Mapped[int]
    currency: Mapped[str] = mapped_column(String(3))
    currency_precision: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Numeric(32, 4))
    allocations: Mapped[dict] = mapped_column(JSONB)
    sources: Mapped[dict] = mapped_column(JSONB)
    costs: Mapped[dict] = mapped_column(JSONB)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ProductionSupplier(Base):
    __tablename__ = "production_suppliers"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive', 'archived') AND version > 0", name="ck_production_suppliers_state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(10))
    version: Mapped[int]
    details: Mapped[dict] = mapped_column(JSONB)
    last_edited_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductionSupplierOffering(Base):
    __tablename__ = "production_supplier_offerings"
    __table_args__ = (
        CheckConstraint("kind IN ('material', 'component', 'service') AND version > 0", name="ck_supplier_offerings_kind"),
        CheckConstraint(
            "(material_id IS NULL OR kind = 'material') AND (definition_id IS NULL OR kind = 'component')",
            name="ck_supplier_offerings_reference",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("production_suppliers.id", ondelete="CASCADE"), index=True)
    material_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("production_material_definitions.id", ondelete="SET NULL"))
    definition_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("spatial_object_definitions.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(10))
    version: Mapped[int]
    active: Mapped[bool]
    details: Mapped[dict] = mapped_column(JSONB)
    last_edited_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductionProjectSupplier(Base):
    __tablename__ = "production_project_suppliers"
    __table_args__ = (CheckConstraint("version > 0", name="ck_project_suppliers_version"),)

    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    supplier_id: Mapped[UUID] = mapped_column(
        ForeignKey("production_suppliers.id", deferrable=True, initially="DEFERRED"), primary_key=True,
    )
    version: Mapped[int]
    active: Mapped[bool]
    note: Mapped[Optional[str]] = mapped_column(Text)
    history: Mapped[list] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductionQuotation(Base):
    __tablename__ = "production_quotations"
    __table_args__ = (
        UniqueConstraint("project_id", "scope_key", "supplier_id", "reference", "version", name="uq_production_quotations_revision"),
        CheckConstraint("version > 0 AND currency_precision BETWEEN 0 AND 4", name="ck_production_quotations_format"),
        CheckConstraint("valid_until IS NULL OR valid_until >= quoted_on", name="ck_production_quotations_dates"),
        CheckConstraint("status IN ('received', 'approved', 'rejected')", name="ck_production_quotations_status"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_production_quotations_currency"),
        CheckConstraint("scope_key = coalesce(event_id::text, 'project')", name="ck_production_quotations_scope"),
        Index("ix_production_quotations_bom", "bom_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    event_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    scope_key: Mapped[str] = mapped_column(String(36))
    bom_id: Mapped[UUID] = mapped_column(ForeignKey("production_boms.id", ondelete="CASCADE"))
    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("production_suppliers.id", deferrable=True, initially="DEFERRED"))
    attachment_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("assets.id", name="fk_production_quotations_attachment", ondelete="SET NULL"), index=True,
    )
    reference: Mapped[str] = mapped_column(String(160))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(10))
    quoted_on: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[Optional[date]] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    currency_precision: Mapped[int]
    details: Mapped[dict] = mapped_column(JSONB)
    lines: Mapped[list] = mapped_column(JSONB)
    audit: Mapped[list] = mapped_column(JSONB)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionProcurement(Base):
    __tablename__ = "production_procurements"
    __table_args__ = (
        UniqueConstraint("bom_line_id", name="uq_production_procurements_bom_line"),
        CheckConstraint(
            "status IN ('requested', 'sourcing', 'approved', 'ordered', 'delivered', 'cancelled') AND version > 0",
            name="ck_production_procurements_state",
        ),
        Index("ix_production_procurements_scope", "project_id", "event_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    event_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    bom_line_id: Mapped[UUID] = mapped_column(ForeignKey("production_bom_lines.id", ondelete="CASCADE"))
    selected_supplier_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("production_suppliers.id", deferrable=True, initially="DEFERRED"),
    )
    quotation_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("production_quotations.id", deferrable=True, initially="DEFERRED"),
    )
    status: Mapped[str] = mapped_column(String(10))
    version: Mapped[int]
    requirement: Mapped[dict] = mapped_column(JSONB)
    selection: Mapped[dict] = mapped_column(JSONB)
    approval: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    audit: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductionValueProposal(Base):
    __tablename__ = "production_value_proposals"
    __table_args__ = (
        CheckConstraint("status IN ('proposed', 'approved', 'rejected', 'applied') AND version > 0", name="ck_value_proposals_state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4, server_default=text("gen_random_uuid()"))
    procurement_id: Mapped[UUID] = mapped_column(ForeignKey("production_procurements.id", ondelete="CASCADE"), index=True)
    quotation_id: Mapped[UUID] = mapped_column(ForeignKey("production_quotations.id", deferrable=True, initially="DEFERRED"))
    material_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("production_material_definitions.id", ondelete="SET NULL"))
    definition_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("spatial_object_definitions.id", ondelete="SET NULL"))
    design_artifact_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("design_artifacts.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10))
    version: Mapped[int]
    proposal: Mapped[dict] = mapped_column(JSONB)
    audit: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
