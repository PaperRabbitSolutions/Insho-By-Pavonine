from app.workers.celery_app import celery_app
from app.services.media_processing import retry_asset_processing


@celery_app.task(name="app.workers.tasks.worker_health_check")
def worker_health_check():
    return {
        "status": "ok",
        "worker": "INSHO",
        "message": "Background task executed successfully"
    }


@celery_app.task(
    bind=True,
    name="app.workers.tasks.process_asset",
    max_retries=2,
    default_retry_delay=2,
)
def process_asset(self, asset_id: str):
    result = retry_asset_processing(asset_id)
    if result.get("reason") == "already_processing":
        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=RuntimeError("processing_already_active"),
                countdown=10 * 60,
            )
    if result.get("status") == "failed" and result.get("retryable"):
        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=RuntimeError(result.get("error_code", "processing_failed")),
                countdown=2 ** self.request.retries,
            )
    return result


@celery_app.task(name="app.workers.tasks.cleanup_deleted_assets")
def cleanup_deleted_assets(retention_days: int = 30, confirm_permanent_delete: bool = False):
    from app.services.asset_lifecycle import cleanup_deleted_assets as cleanup

    return cleanup(
        retention_days=retention_days,
        confirm_permanent_delete=confirm_permanent_delete,
    )
