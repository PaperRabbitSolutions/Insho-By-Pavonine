"""Project/event design history over existing domain records."""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignSnapshot(Base):
    __tablename__ = "design_snapshots"
    __table_args__ = (UniqueConstraint("id", "project_id", "scope_key", name="uq_design_snapshots_scope"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", name="fk_design_snapshots_project", ondelete="RESTRICT"))
    scope_key: Mapped[str] = mapped_column(String(36))
    state: Mapped[dict] = mapped_column(JSONB)


class DesignSnapshotAsset(Base):
    __tablename__ = "design_snapshot_assets"

    snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("design_snapshots.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True, index=True)


class DesignBranch(Base):
    __tablename__ = "design_branches"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "scope_key", name="uq_design_branches_scope"),
        ForeignKeyConstraint(["source_version_id", "project_id", "scope_key"],
                             ["design_versions.id", "design_versions.project_id", "design_versions.scope_key"],
                             use_alter=True, name="fk_design_branches_source"),
        ForeignKeyConstraint(["base_mainline_id", "project_id", "scope_key"],
                             ["design_versions.id", "design_versions.project_id", "design_versions.scope_key"],
                             use_alter=True, name="fk_design_branches_mainline"),
        ForeignKeyConstraint(["parent_branch_id", "project_id", "scope_key"],
                             ["design_branches.id", "design_branches.project_id", "design_branches.scope_key"],
                             name="fk_design_branches_parent"),
        ForeignKeyConstraint(["event_id", "project_id"], ["events.id", "events.project_id"],
                             ondelete="RESTRICT", name="fk_design_branches_event"),
        CheckConstraint("scope_key = coalesce(event_id::text, 'project')", name="ck_design_branches_scope"),
        CheckConstraint("status IN ('active', 'promoted', 'archived')", name="ck_design_branches_status"),
        Index("ix_design_branches_project_scope_created", "project_id", "scope_key", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", name="fk_design_branches_project", ondelete="RESTRICT"))
    event_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    scope_key: Mapped[str] = mapped_column(String(36))
    source_version_id: Mapped[UUID] = mapped_column(Uuid)
    base_mainline_id: Mapped[UUID] = mapped_column(Uuid)
    parent_branch_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    live_basis: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DesignVersion(Base):
    __tablename__ = "design_versions"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "scope_key", name="uq_design_versions_scope"),
        UniqueConstraint("id", "project_id", name="uq_design_versions_id_project"),
        UniqueConstraint("project_id", "scope_key", "number", name="uq_design_versions_number"),
        ForeignKeyConstraint(["snapshot_id", "project_id", "scope_key"],
                             ["design_snapshots.id", "design_snapshots.project_id", "design_snapshots.scope_key"],
                             name="fk_design_versions_snapshot"),
        ForeignKeyConstraint(["branch_id", "project_id", "scope_key"],
                             ["design_branches.id", "design_branches.project_id", "design_branches.scope_key"],
                             name="fk_design_versions_branch"),
        ForeignKeyConstraint(["parent_id", "project_id", "scope_key"],
                             ["design_versions.id", "design_versions.project_id", "design_versions.scope_key"],
                             name="fk_design_versions_parent"),
        ForeignKeyConstraint(["based_on_id", "project_id", "scope_key"],
                             ["design_versions.id", "design_versions.project_id", "design_versions.scope_key"],
                             name="fk_design_versions_based_on"),
        ForeignKeyConstraint(["event_id", "project_id"], ["events.id", "events.project_id"],
                             ondelete="RESTRICT", name="fk_design_versions_event"),
        CheckConstraint("scope_key = coalesce(event_id::text, 'project') AND number > 0", name="ck_design_versions_scope_number"),
        CheckConstraint("id <> parent_id AND id <> based_on_id", name="ck_design_versions_ancestry"),
        Index("ix_design_versions_head", "project_id", "scope_key", "branch_id", "number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", name="fk_design_versions_project", ondelete="RESTRICT"))
    event_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    scope_key: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    number: Mapped[int]
    parent_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    based_on_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    summary: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DesignVersionReview(Base):
    __tablename__ = "design_version_reviews"
    __table_args__ = (
        UniqueConstraint("presentation_id", "presentation_revision", name="uq_design_review_presentation_revision"),
        ForeignKeyConstraint(["presentation_id", "presentation_revision"],
                             ["presentation_revisions.presentation_id", "presentation_revisions.version"],
                             name="fk_design_review_presentation_revision", ondelete="RESTRICT"),
        ForeignKeyConstraint(["version_id", "project_id"], ["design_versions.id", "design_versions.project_id"],
                             name="fk_design_review_version_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(["presentation_id", "project_id"], ["presentations.id", "presentations.project_id"],
                             name="fk_design_review_presentation_project", ondelete="RESTRICT"),
    )

    version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid)
    presentation_id: Mapped[UUID] = mapped_column(Uuid)
    presentation_revision: Mapped[int]


class DesignActivity(Base):
    __tablename__ = "design_activities"
    __table_args__ = (Index("ix_design_activities_project_created", "project_id", "created_at", "id"),
                      Index("ix_design_activities_branch", "branch_id"), Index("ix_design_activities_version", "version_id"))

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    # Retain the original event identifier even when a memory-only event is removed.
    event_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    version_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("design_versions.id", ondelete="RESTRICT"))
    branch_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("design_branches.id", ondelete="RESTRICT"))
    actor_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    execution_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("ai_executions.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB)
    request_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
