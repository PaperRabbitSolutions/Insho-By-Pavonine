
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, Text, Numeric, Uuid,
    ForeignKey, DateTime, func, text
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VenueZone(Base):
    __tablename__ = "venue_zones"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    area: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
