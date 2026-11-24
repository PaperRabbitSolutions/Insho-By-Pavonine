
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy import (
    String, Text, Uuid,
    ForeignKey, DateTime, func, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VenueSource(Base):
    __tablename__ = "venue_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )

    venue_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    source_type: Mapped[str] = mapped_column(String(50), nullable=False)

    file_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
