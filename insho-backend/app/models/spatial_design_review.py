"""Reviewed actions and their atomic canonical scene receipts."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SpatialDesignReview(Base):
    __tablename__ = "spatial_design_reviews"
    __table_args__ = (
        CheckConstraint("status IN ('validated', 'applied', 'rejected')", name="ck_spatial_design_review_status"),
        CheckConstraint("base_revision > 0", name="ck_spatial_design_review_revision"),
        CheckConstraint(
            "outcome IS NOT NULL AND ((status = 'rejected' AND action IS NULL AND policy_fingerprint IS NULL) "
            "OR (status IN ('validated', 'applied') AND action IS NOT NULL AND policy_fingerprint IS NOT NULL))",
            name="ck_spatial_design_review_payload",
        ),
    )

    scene_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("spatial_scenes.id", ondelete="CASCADE"), primary_key=True)
    recommendation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    review_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    context_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    policy_fingerprint: Mapped[Optional[str]] = mapped_column(String(64))
    outcome: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    reviewed_by_user_id: Mapped[Optional[UUID]] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
