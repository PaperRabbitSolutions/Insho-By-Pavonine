import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignArtifact(Base):
    __tablename__ = "design_artifacts"
    __table_args__ = (
        CheckConstraint("module IN ('mood', 'style', 'plan', 'vision', 'elements')", name="ck_design_artifacts_module"),
        CheckConstraint("status IN ('draft', 'approved', 'archived')", name="ck_design_artifacts_status"),
        UniqueConstraint("project_id", "event_scope_key", "module", "version", name="uq_design_artifacts_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True)
    # A stable non-null scope key lets PostgreSQL enforce version uniqueness for project-wide artifacts too.
    event_scope_key: Mapped[str] = mapped_column(String(36), nullable=False)
    module: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    based_on: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="ai_generated", server_default="ai_generated")
    ai_execution_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("ai_executions.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DesignElement(Base):
    __tablename__ = "design_elements"
    __table_args__ = (
        CheckConstraint("category IN ('floral', 'decor', 'furniture', 'lighting', 'scenic', 'architectural', 'stage', 'fabric', 'flooring', 'structure', 'prop', 'other')", name="ck_design_elements_category"),
        CheckConstraint("source IN ('ai_suggested', 'user_confirmed')", name="ck_design_elements_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("design_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="ai_suggested", server_default="ai_suggested")
    confirmed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DesignArtifactAsset(Base):
    __tablename__ = "design_artifact_assets"
    __table_args__ = (
        CheckConstraint("role IN ('reference', 'generated')", name="ck_design_artifact_assets_role"),
        UniqueConstraint("artifact_id", "asset_id", name="uq_design_artifact_asset"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("design_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="reference", server_default="reference")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
