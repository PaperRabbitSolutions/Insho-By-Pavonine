from datetime import datetime, timezone

from app.db.database import SessionLocal
from app.models.asset import Asset


def enqueue_asset_processing(asset_id) -> bool:
    """Publish after the Asset commit; queue outages never roll back file upload."""
    try:
        from app.workers.tasks import process_asset

        process_asset.apply_async(args=[str(asset_id)])
        return True
    except Exception:
        db = SessionLocal()
        try:
            asset = db.get(Asset, asset_id)
            if asset and asset.processing_status == "pending":
                asset.processing_status = "failed"
                asset.processing_error_code = "queue_unavailable"
                asset.processing_completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
        return False
