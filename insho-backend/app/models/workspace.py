
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String,
    Text,
    DateTime,
    Uuid,
    ForeignKey,
    UniqueConstraint,
    func
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_organization_workspace_slug"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False
    )

    slug: Mapped[str] = mapped_column(
        String(180),
        nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
