"""Insho Present identities, immutable sheets, and revision-scoped reviews."""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Presentation(Base):
    __tablename__ = "presentations"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_presentations_id_project"),
        ForeignKeyConstraint(["event_id", "project_id"], ["events.id", "events.project_id"],
                             name="fk_presentations_event_project", ondelete="RESTRICT"),
        ForeignKeyConstraint(["alternative_of_id", "project_id"], ["presentations.id", "presentations.project_id"],
                             name="fk_presentations_alternative_project"),
        CheckConstraint("id <> alternative_of_id", name="ck_presentations_alternative"),
        Index("ix_presentations_project_event_created", "project_id", "event_id", "created_at", "id"),
        Index("ix_presentations_alternative_of_id", "alternative_of_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", name="fk_presentations_project", ondelete="RESTRICT"))
    event_id: Mapped[Optional[UUID]] = mapped_column(Uuid, index=True)
    alternative_of_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PresentationRevision(Base):
    __tablename__ = "presentation_revisions"
    __table_args__ = (CheckConstraint("version > 0", name="ck_presentation_revisions_version"),)

    presentation_id: Mapped[UUID] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    sheet: Mapped[dict] = mapped_column(JSONB)
    evidence: Mapped[dict] = mapped_column(JSONB)
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PresentationAsset(Base):
    __tablename__ = "presentation_assets"
    __table_args__ = (
        ForeignKeyConstraint(["presentation_id", "revision"], ["presentation_revisions.presentation_id", "presentation_revisions.version"],
                             ondelete="CASCADE", name="fk_presentation_assets_revision"),
        Index("ix_presentation_assets_asset_id", "asset_id"),
    )

    presentation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), primary_key=True)


class PresentationCheckpoint(Base):
    __tablename__ = "presentation_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(["presentation_id", "revision"], ["presentation_revisions.presentation_id", "presentation_revisions.version"],
                             ondelete="CASCADE", name="fk_presentation_checkpoints_revision"),
        Index("ix_presentation_checkpoints_revision", "presentation_id", "revision", "created_at", "id"),
        CheckConstraint(
            "(status = 'draft' AND requested_at IS NULL AND decided_at IS NULL) OR "
            "(status = 'pending' AND requested_at IS NOT NULL AND decided_at IS NULL) OR "
            "(status IN ('approved', 'rejected', 'revision_requested') AND requested_at IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_presentation_checkpoints_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    presentation_id: Mapped[UUID] = mapped_column(Uuid)
    revision: Mapped[int]
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20))
    created_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    requested_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    request_comment: Mapped[Optional[str]] = mapped_column(Text)
    decided_by_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[Optional[str]] = mapped_column(Text)
