
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy import (
    Text, Uuid, ForeignKey, DateTime, func, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectMemory(Base):
    __tablename__ = "project_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )

    memory_data: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
        default=dict, server_default=text("'{}'::jsonb")
    )

    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
