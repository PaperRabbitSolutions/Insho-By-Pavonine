
import uuid

from datetime import datetime

from sqlalchemy import (
    String,
    DateTime,
    Uuid,
    ForeignKey,
    UniqueConstraint,
    func
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OrganizationMember(Base):
    __tablename__ = "organization_members"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "organization_id",
            name="uq_user_organization"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="member"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
