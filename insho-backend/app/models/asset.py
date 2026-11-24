import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, Uuid, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "category IN ('image', 'video', 'document', 'design', 'floor_plan', "
            "'venue_reference', 'inspiration', 'audio', 'other')",
            name="ck_assets_category",
        ),
        CheckConstraint("status IN ('active', 'deleted')", name="ck_assets_status"),
        CheckConstraint("file_size > 0", name="ck_assets_file_size_positive"),
        CheckConstraint(
            "processing_status IN ('not_started', 'pending', 'processing', 'complete', 'failed', 'not_applicable')",
            name="ck_assets_processing_status",
        ),
        UniqueConstraint("storage_key", name="uq_assets_storage_key"),
        Index("ix_assets_org_created", "organization_id", "created_at"),
        Index("ix_assets_scope_status", "organization_id", "project_id", "event_id", "status"),
        Index("ix_assets_folder", "folder_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    folder_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("asset_folders.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(30), nullable=False, default="local")
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    duplicate_of_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    category: Mapped[str] = mapped_column(String(30), nullable=False, default="other", server_default="other")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_started", server_default="not_started"
    )
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processing_error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    media_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    uploaded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
