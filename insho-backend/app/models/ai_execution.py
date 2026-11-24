import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIExecution(Base):
    __tablename__ = "ai_executions"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", "idempotency_key", name="uq_ai_execution_idempotency"),
        Index("ix_ai_executions_org_created", "organization_id", "created_at"),
        Index("ix_ai_executions_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    task: Mapped[str] = mapped_column(String(80), nullable=False)
    feature: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    output_format: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(12, 8), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_from: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
