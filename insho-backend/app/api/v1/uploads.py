from typing import Iterator, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.schemas.upload import UploadResponse
from app.services.storage import StorageError, StorageProvider, get_storage_provider
from app.services.upload import UploadService


router = APIRouter(prefix="/uploads", tags=["Uploads"])


def _configured_storage() -> StorageProvider:
    try:
        return get_storage_provider()
    except (RuntimeError, StorageError) as exc:
        raise HTTPException(
            status_code=503, detail="File storage is not configured."
        ) from exc


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    organization_id: UUID = Form(...),
    file: UploadFile = File(...),
    project_id: Optional[UUID] = Form(default=None),
    event_id: Optional[UUID] = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StorageProvider = Depends(_configured_storage),
):
    return await UploadService.upload(
        db=db,
        user_id=current_user.id,
        organization_id=organization_id,
        project_id=project_id,
        event_id=event_id,
        upload_file=file,
        provider=provider,
    )


def _file_chunks(source, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    try:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        source.close()


@router.get("/{storage_key}")
async def retrieve_file(
    storage_key: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    provider: StorageProvider = Depends(_configured_storage),
):
    source, metadata = await run_in_threadpool(
        UploadService.retrieve,
        db=db,
        user_id=current_user.id,
        storage_key=storage_key,
        provider=provider,
    )
    content_type = metadata.get("content_type")
    filename = metadata.get("original_filename")
    size = metadata.get("size")
    if not isinstance(content_type, str) or not isinstance(filename, str) or not isinstance(size, int):
        source.close()
        raise HTTPException(status_code=404, detail="Stored file not found.")

    safe_filename = quote(filename, safe="")
    response_type = "application/octet-stream" if filename.lower().endswith(".svg") else content_type
    return StreamingResponse(
        _file_chunks(source),
        media_type=response_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
            "Content-Length": str(size),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
