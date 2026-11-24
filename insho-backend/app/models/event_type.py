
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Uuid, DateTime, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EventType(Base):
    __tablename__ = "event_types"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()")
    )

    name: Mapped[str] = mapped_column(
        String(150), unique=True, nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
