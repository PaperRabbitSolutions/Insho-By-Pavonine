import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetFolder(Base):
    __tablename__ = "asset_folders"
    __table_args__ = (
        Index("ix_asset_folders_scope", "organization_id", "workspace_id", "project_id", "parent_folder_id"),
        Index(
            "uq_asset_folders_scope_parent_name",
            text("organization_id"),
            text("COALESCE(workspace_id::text, '')"),
            text("COALESCE(project_id::text, '')"),
            text("COALESCE(parent_folder_id::text, '')"),
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
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
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True,
    )
    parent_folder_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("asset_folders.id", ondelete="SET NULL"), nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
