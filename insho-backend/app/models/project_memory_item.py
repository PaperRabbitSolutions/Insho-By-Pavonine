import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectMemoryItem(Base):
    __tablename__ = "project_memory_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('decision', 'preference', 'constraint', 'requirement', 'fact', 'note')",
            name="ck_project_memory_items_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_project_memory_items_status",
        ),
        CheckConstraint(
            "jsonb_typeof(content) = 'object' AND content <> '{}'::jsonb",
            name="ck_project_memory_items_content_object",
        ),
        CheckConstraint("version > 0 AND verification IN ('unverified', 'verified')", name="ck_memory_items_revision_verification"),
        CheckConstraint("category IN ('must_have','must_avoid','approved','rejected','client_preferences','design_dna',"
                        "'budget','venue_dimensions','guest_count','previous_decisions','vendor_constraints',"
                        "'production_constraints','safety_constraints')", name="ck_memory_items_category"),
        Index("ix_memory_items_project_category_active", "project_id", "category", "status", "created_at"),
        ForeignKeyConstraint(["linked_version_id", "project_id"], ["design_versions.id", "design_versions.project_id"],
                             name="fk_memory_design_version", ondelete="RESTRICT"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="user", server_default="user"
    )
    source_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(24))
    description: Mapped[Optional[str]] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(String(10), default="unverified", server_default="unverified")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    linked_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("presentation_checkpoints.id", name="fk_memory_decision", ondelete="RESTRICT"))
    execution_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_executions.id", name="fk_memory_execution", ondelete="SET NULL"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectMemoryRevision(Base):
    __tablename__ = "project_memory_revisions"
    __table_args__ = (CheckConstraint("version > 0", name="ck_project_memory_revisions_version"),)

    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_memory_items.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[dict] = mapped_column(JSONB)
    action: Mapped[str] = mapped_column(String(30))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    changed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
