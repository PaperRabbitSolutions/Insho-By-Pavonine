from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.asset_derivative import AssetDerivative
from app.models.design_version import DesignSnapshotAsset
from app.models.object_library import ObjectDefinitionAsset
from app.models.presentation import PresentationAsset
from app.models.spatial import SpatialObjectAsset
from app.models.production_procurement import ProductionQuotation
from app.services.storage import ObjectNotFound, StorageError, StorageProvider, get_storage_provider


logger = logging.getLogger(__name__)


def cleanup_deleted_assets(
    *,
    retention_days: int = 30,
    confirm_permanent_delete: bool = False,
    batch_size: int = 100,
    db: Session = None,
    provider: StorageProvider = None,
) -> Dict[str, Any]:
    """Permanently remove old soft-deleted assets; dry-run unless explicitly confirmed."""
    if retention_days < 1 or retention_days > 3650 or batch_size < 1 or batch_size > 500:
        raise ValueError("Cleanup limits are outside the supported range")
    owns_session = db is None
    if owns_session:
        from app.db.database import SessionLocal

        db = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    skipped = 0
    failed = 0
    try:
        query = (
            select(Asset)
            .where(
                Asset.status == "deleted",
                Asset.deleted_at < cutoff,
                Asset.processing_status != "processing",
                ~select(ObjectDefinitionAsset.asset_id).where(ObjectDefinitionAsset.asset_id == Asset.id).exists(),
                ~select(SpatialObjectAsset.asset_id).where(SpatialObjectAsset.asset_id == Asset.id).exists(),
                ~select(ProductionQuotation.attachment_id).where(ProductionQuotation.attachment_id == Asset.id).exists(),
                ~select(PresentationAsset.asset_id).where(PresentationAsset.asset_id == Asset.id).exists(),
                ~select(DesignSnapshotAsset.asset_id).where(DesignSnapshotAsset.asset_id == Asset.id).exists(),
            )
            .order_by(Asset.deleted_at.asc())
            .limit(batch_size)
        )
        candidates = db.scalars(query.with_only_columns(Asset.id)).all()
        if not confirm_permanent_delete:
            logger.info("Asset cleanup dry run: %s eligible assets", len(candidates))
            return {"dry_run": True, "eligible": len(candidates), "removed": 0, "skipped": 0, "failed": 0}
        provider = provider or get_storage_provider()

        for asset_id in candidates:
            # Each prior purge may have committed. Recheck references while holding this row lock.
            asset = db.scalars(query.where(Asset.id == asset_id).with_for_update(skip_locked=True)
                               .execution_options(populate_existing=True)).first()
            if asset is None:
                skipped += 1
                continue
            active_reference = db.scalar(
                select(Asset.id).where(
                    Asset.storage_key == asset.storage_key,
                    Asset.id != asset.id,
                    Asset.deleted_at.is_(None),
                ).limit(1)
            )
            if active_reference is not None:
                skipped += 1
                continue
            derivatives = db.scalars(
                select(AssetDerivative).where(AssetDerivative.asset_id == asset.id)
            ).all()
            keys = [asset.storage_key] + [item.storage_key for item in derivatives]
            conflicting_reference = db.scalar(
                select(Asset.id).where(
                    Asset.id != asset.id,
                    Asset.status == "active",
                    Asset.storage_key.in_(keys),
                ).limit(1)
            )
            if conflicting_reference is not None:
                skipped += 1
                continue
            try:
                for key in keys:
                    try:
                        provider.delete(key)
                    except ObjectNotFound:
                        pass
                db.delete(asset)
                db.commit()
                removed += 1
            except StorageError:
                db.rollback()
                failed += 1
            except Exception:
                db.rollback()
                failed += 1
        result = {
            "dry_run": False,
            "eligible": len(candidates),
            "removed": removed,
            "skipped": skipped,
            "failed": failed,
        }
        logger.info(
            "Asset cleanup finished: eligible=%s removed=%s skipped=%s failed=%s",
            len(candidates), removed, skipped, failed,
        )
        return result
    finally:
        if owns_session:
            db.close()
