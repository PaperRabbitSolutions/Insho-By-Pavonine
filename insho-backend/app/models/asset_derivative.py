import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssetDerivative(Base):
    __tablename__ = "asset_derivatives"
    __table_args__ = (
        CheckConstraint("kind IN ('thumbnail', 'preview', 'poster')", name="ck_asset_derivatives_kind"),
        CheckConstraint("file_size > 0", name="ck_asset_derivatives_file_size_positive"),
        UniqueConstraint("asset_id", "kind", name="uq_asset_derivatives_asset_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    storage_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
