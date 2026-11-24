from datetime import datetime
from typing import Iterator, Literal, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.api.v1.uploads import _configured_storage
from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.models.asset_derivative import AssetDerivative
from app.schemas.asset import (
    AssetDerivativeResponse,
    AssetFolderCreate,
    AssetFolderResponse,
    AssetFolderUpdate,
    AssetMove,
    AssetResponse,
    AssetUpdate,
    AssetProcessingResponse,
    StorageUsageResponse,
)
from app.services.asset import AssetService
from app.services.storage import ObjectNotFound, StorageError, StorageProvider


router = APIRouter(prefix="/assets", tags=["Assets"])


def _chunks(source, size: int = 1024 * 1024) -> Iterator[bytes]:
    try:
        while True:
            data = source.read(size)
            if not data:
                break
            yield data
    finally:
        source.close()


@router.get("", response_model=list[AssetResponse])
def list_assets(
    organization_id: UUID,
    workspace_id: Optional[UUID] = None,
    project_id: Optional[UUID] = None,
    event_id: Optional[UUID] = None,
    folder_id: Optional[UUID] = None,
    category: Optional[str] = None,
    mime_type: Optional[str] = None,
    extension: Optional[str] = None,
    status_filter: Literal["active", "deleted", "all"] = Query(default="active", alias="status"),
    q: Optional[str] = Query(default=None, max_length=200),
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    sort_by: Literal["created_at", "updated_at", "name", "file_size"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.list_assets(
        db, current_user.id, organization_id,
        workspace_id=workspace_id, project_id=project_id, event_id=event_id,
        folder_id=folder_id, category=category, mime_type=mime_type,
        extension=extension, status=status_filter, q=q,
        created_from=created_from, created_to=created_to,
        sort_by=sort_by, sort_order=sort_order, offset=offset, limit=limit,
    )


@router.get("/usage", response_model=StorageUsageResponse)
def storage_usage(
    organization_id: UUID,
    project_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.storage_usage(db, current_user.id, organization_id, project_id)


@router.post("/folders", response_model=AssetFolderResponse, status_code=status.HTTP_201_CREATED)
def create_folder(
    data: AssetFolderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.create_folder(db, current_user.id, data)


@router.get("/folders", response_model=list[AssetFolderResponse])
def list_folders(
    organization_id: UUID,
    workspace_id: Optional[UUID] = None,
    project_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.list_folders(
        db, current_user.id, organization_id,
        workspace_id=workspace_id, project_id=project_id,
    )


@router.get("/folders/{folder_id}", response_model=AssetFolderResponse)
def get_folder(
    folder_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.get_folder(db, folder_id, current_user.id)


@router.patch("/folders/{folder_id}", response_model=AssetFolderResponse)
def update_folder(
    folder_id: UUID,
    data: AssetFolderUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.update_folder(db, folder_id, current_user.id, data)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_folder(
    folder_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    AssetService.delete_folder(db, folder_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{asset_id}/download")
def download_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StorageProvider = Depends(_configured_storage),
):
    asset = AssetService.get(db, asset_id, current_user.id)
    try:
        source = provider.open(asset.storage_key)
    except ObjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Stored file not found") from exc
    except StorageError as exc:
        raise HTTPException(status_code=503, detail="File storage is temporarily unavailable") from exc
    filename = quote(asset.original_filename, safe="")
    content_type = "application/octet-stream" if asset.file_extension == ".svg" else asset.mime_type
    return StreamingResponse(
        _chunks(source),
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Content-Length": str(asset.file_size),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{asset_id}/processing", response_model=AssetProcessingResponse)
def processing_status(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    asset = AssetService.get(db, asset_id, current_user.id)
    derivatives = db.scalars(
        select(AssetDerivative)
        .where(AssetDerivative.asset_id == asset.id)
        .order_by(AssetDerivative.kind.asc())
    ).all()
    return {
        "asset_id": asset.id,
        "status": asset.processing_status,
        "attempts": asset.processing_attempts,
        "error_code": asset.processing_error_code,
        "started_at": asset.processing_started_at,
        "completed_at": asset.processing_completed_at,
        "metadata": asset.media_metadata,
        "derivatives": derivatives,
    }


@router.post("/{asset_id}/processing/retry", response_model=AssetResponse)
def retry_processing(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.retry_processing(db, asset_id, current_user.id)


@router.get("/{asset_id}/preview/{kind}")
def get_preview(
    asset_id: UUID,
    kind: Literal["thumbnail", "preview", "poster"],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StorageProvider = Depends(_configured_storage),
):
    derivative = AssetService.derivative(db, asset_id, current_user.id, kind)
    try:
        source = provider.open(derivative.storage_key)
    except ObjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Asset preview is unavailable") from exc
    except StorageError as exc:
        raise HTTPException(status_code=503, detail="File storage is temporarily unavailable") from exc
    return StreamingResponse(
        _chunks(source),
        media_type="image/jpeg",
        headers={
            "Content-Length": str(derivative.file_size),
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.get(db, asset_id, current_user.id)


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: UUID,
    data: AssetUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.update(db, asset_id, current_user.id, data)


@router.post("/{asset_id}/move", response_model=AssetResponse)
def move_asset(
    asset_id: UUID,
    data: AssetMove,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.move(db, asset_id, current_user.id, data)


@router.delete("/{asset_id}", response_model=AssetResponse)
def delete_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.delete(db, asset_id, current_user.id)


@router.post("/{asset_id}/restore", response_model=AssetResponse)
def restore_asset(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return AssetService.restore(db, asset_id, current_user.id)
