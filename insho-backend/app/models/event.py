
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, Date, DateTime, Numeric,
    Uuid, ForeignKey, UniqueConstraint, func, text
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_events_id_project"),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # Nullable to retain legacy events created before project links existed.
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True, index=True
    )

    venue_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("venues.id", ondelete="SET NULL"),
        nullable=True, index=True
    )

    event_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("event_types.id", ondelete="SET NULL"),
        nullable=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    guest_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", server_default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
