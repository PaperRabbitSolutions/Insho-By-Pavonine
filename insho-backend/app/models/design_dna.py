
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any

from sqlalchemy import (
    Integer, Numeric, String, Uuid,
    ForeignKey, DateTime, func, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DesignDNA(Base):
    __tablename__ = "design_dna"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )

    palette: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    materials: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    floral: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    lighting: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    architecture: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    motifs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    cultural_inspiration: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)

    mood: Mapped[Optional[str]] = mapped_column(String(150))
    formality: Mapped[Optional[str]] = mapped_column(String(100))

    luxury: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))

    version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
