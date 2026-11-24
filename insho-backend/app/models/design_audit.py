from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignAudit(Base):
    __tablename__ = "design_audits"
    __table_args__ = (
        ForeignKeyConstraint(["design_version_id", "project_id"], ["design_versions.id", "design_versions.project_id"],
                             name="fk_design_audits_version_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(["event_id", "project_id"], ["events.id", "events.project_id"],
                             name="fk_design_audits_event_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(["retry_of_id", "design_version_id"], ["design_audits.id", "design_audits.design_version_id"],
                             name="fk_design_audits_retry_version"),
        UniqueConstraint("id", "design_version_id", name="uq_design_audits_id_version"),
        CheckConstraint("(status = 'running' AND completed_at IS NULL) OR "
                        "(status IN ('completed','completed_with_warnings','failed','cancelled') AND completed_at IS NOT NULL)",
                        name="ck_design_audits_status"),
        CheckConstraint("CASE WHEN status IN ('completed','completed_with_warnings') "
                        "THEN summary IS NOT NULL AND jsonb_array_length(results) = 18 "
                        "ELSE summary IS NULL AND jsonb_array_length(results) = 0 END", name="ck_design_audits_results"),
        Index("ix_design_audits_project_created", "project_id", "started_at", "id"),
        Index("ix_design_audits_version_created", "design_version_id", "started_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    project_id: Mapped[UUID] = mapped_column(Uuid)
    event_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    design_version_id: Mapped[UUID] = mapped_column(Uuid)
    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("design_snapshots.id", ondelete="RESTRICT"))
    initiated_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    retry_of_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(28))
    configuration_version: Mapped[str] = mapped_column(String(24))
    options: Mapped[dict] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    results: Mapped[list] = mapped_column(JSONB)
    summary: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    warnings: Mapped[list] = mapped_column(JSONB)
    retryable: Mapped[bool]
    evidence_sources: Mapped[list] = mapped_column(JSONB)
    ai_execution_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("ai_executions.id", ondelete="SET NULL"))
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    model: Mapped[Optional[str]] = mapped_column(String(150))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
