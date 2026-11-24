"""Tenant-scoped actuals, immutable learning reports and review history."""

from datetime import date, datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LearningActual(Base):
    __tablename__ = "learning_actuals"
    __table_args__ = (
        ForeignKeyConstraint(["event_id", "project_id"], ["events.id", "events.project_id"],
                             name="fk_learning_actual_event", ondelete="RESTRICT"),
        ForeignKeyConstraint(["design_version_id", "project_id"], ["design_versions.id", "design_versions.project_id"],
                             name="fk_learning_actual_version", ondelete="RESTRICT"),
        ForeignKeyConstraint(["supersedes_id", "event_id", "project_id"],
                             ["learning_actuals.id", "learning_actuals.event_id", "learning_actuals.project_id"],
                             name="fk_learning_actual_predecessor", ondelete="RESTRICT"),
        UniqueConstraint("id", "event_id", "project_id", name="uq_learning_actual_scope"),
        CheckConstraint("(design_version_id IS NULL) = (snapshot_id IS NULL)", name="ck_learning_actual_snapshot"),
        CheckConstraint("(status = 'draft' AND verified_at IS NULL AND review_note IS NULL) OR "
                        "(status = 'verified' AND verified_at IS NOT NULL AND review_note IS NOT NULL)",
                        name="ck_learning_actual_status"),
        CheckConstraint("revision > 0 AND supersedes_id IS DISTINCT FROM id", name="ck_learning_actual_revision"),
        CheckConstraint("origin IN ('observed', 'reported') AND data->>'kind' = kind", name="ck_learning_actual_origin"),
        Index("ix_learning_actual_event_history", "project_id", "event_id", "created_at", "id"),
        Index("ix_learning_actual_inputs", "project_id", "event_id", "design_version_id", "status", "kind"),
        Index("uq_learning_actual_verified_correction", "supersedes_id", unique=True,
              postgresql_where=text("status = 'verified' AND supersedes_id IS NOT NULL")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    project_id: Mapped[UUID] = mapped_column(Uuid)
    event_id: Mapped[UUID] = mapped_column(Uuid)
    design_version_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    snapshot_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("design_snapshots.id", ondelete="RESTRICT"))
    supersedes_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int]
    status: Mapped[str] = mapped_column(String(12))
    origin: Mapped[str] = mapped_column(String(12))
    source_reference: Mapped[Optional[str]] = mapped_column(String(2000))
    data: Mapped[dict] = mapped_column(JSONB)
    derived: Mapped[dict] = mapped_column(JSONB)
    sources: Mapped[list] = mapped_column(JSONB)
    occurred_on: Mapped[date]
    recorded_by_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_by_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    review_note: Mapped[Optional[str]] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class LearningReport(Base):
    __tablename__ = "learning_reports"
    __table_args__ = (
        ForeignKeyConstraint(["event_id", "project_id"], ["events.id", "events.project_id"],
                             name="fk_learning_report_event", ondelete="RESTRICT"),
        ForeignKeyConstraint(["design_version_id", "project_id"], ["design_versions.id", "design_versions.project_id"],
                             name="fk_learning_report_version", ondelete="RESTRICT"),
        UniqueConstraint("id", "project_id", name="uq_learning_report_project"),
        UniqueConstraint("project_id", "kind", "idempotency_key", name="uq_learning_report_idempotency"),
        CheckConstraint("(kind = 'reconciliation' AND event_id IS NOT NULL AND design_version_id IS NOT NULL AND snapshot_id IS NOT NULL) OR "
                        "(kind = 'insights' AND event_id IS NULL AND design_version_id IS NULL AND snapshot_id IS NULL)",
                        name="ck_learning_report_kind"),
        Index("ix_learning_report_history", "project_id", "kind", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    event_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    event_ids: Mapped[list] = mapped_column(JSONB)
    design_version_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    snapshot_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("design_snapshots.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    method_version: Mapped[str] = mapped_column(String(24))
    content: Mapped[dict] = mapped_column(JSONB)
    warnings: Mapped[list] = mapped_column(JSONB)
    ai_execution_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("ai_executions.id", ondelete="SET NULL"))
    initiated_by_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningDisposition(Base):
    __tablename__ = "learning_dispositions"
    __table_args__ = (
        ForeignKeyConstraint(["report_id", "project_id"], ["learning_reports.id", "learning_reports.project_id"],
                             name="fk_learning_disposition_report", ondelete="RESTRICT"),
        CheckConstraint("decision IN ('accepted_for_review','dismissed','deferred')", name="ck_learning_disposition_decision"),
        Index("ix_learning_disposition_history", "report_id", "insight_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(Uuid)
    report_id: Mapped[UUID] = mapped_column(Uuid)
    insight_id: Mapped[UUID] = mapped_column(Uuid)
    actor_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
