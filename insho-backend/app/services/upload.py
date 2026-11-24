import tempfile
import uuid
import hashlib
from typing import Any, Dict, Optional

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.config import get_storage_settings
from app.models.asset import Asset
from app.models.event import Event
from app.models.project import Project
from app.security import require_event_access, require_membership, require_project_access
from app.services.storage import (
    InvalidStorageKey,
    ObjectAlreadyExists,
    ObjectNotFound,
    StorageError,
    StorageProvider,
)
from app.services.upload_validation import (
    UploadValidationError,
    validate_declared_type,
    validate_file_content,
)
from app.schemas.upload import UploadResponse
from sqlalchemy import select
from sqlalchemy.orm import Session


_READ_CHUNK_SIZE = 64 * 1024
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024
_KEY_COLLISION_ATTEMPTS = 5


class UploadService:

    @staticmethod
    def _validate_scope(
        db: Session,
        user_id,
        organization_id,
        project_id,
        event_id,
        *,
        write: bool,
    ) -> Dict[str, Optional[str]]:
        require_membership(db, user_id, organization_id, write=write)
        project: Optional[Project] = None
        event: Optional[Event] = None

        if project_id is not None:
            project = require_project_access(db, user_id, project_id, write=write)
            if project.organization_id != organization_id:
                raise HTTPException(status_code=404, detail="Resource not found")

        if event_id is not None:
            event = require_event_access(db, user_id, event_id, write=write)
            if event.organization_id != organization_id:
                raise HTTPException(status_code=404, detail="Resource not found")
            if project is not None and event.project_id != project.id:
                raise HTTPException(status_code=404, detail="Resource not found")
            if event.project_id is not None:
                event_project = require_project_access(
                    db, user_id, event.project_id, write=write
                )
                if event_project.organization_id != organization_id:
                    raise HTTPException(status_code=404, detail="Resource not found")
                project = event_project

        return {
            "organization_id": organization_id,
            "workspace_id": project.workspace_id if project else None,
            "project_id": project.id if project else None,
            "event_id": event.id if event else None,
        }

    @classmethod
    async def upload(
        cls,
        *,
        db: Session,
        user_id,
        organization_id,
        project_id,
        event_id,
        upload_file: UploadFile,
        provider: StorageProvider,
    ) -> UploadResponse:
        try:
            settings = get_storage_settings()
            validated = validate_declared_type(
                upload_file.filename or "",
                upload_file.content_type or "",
                settings,
            )
            scope = await run_in_threadpool(
                cls._validate_scope,
                db,
                user_id,
                organization_id,
                project_id,
                event_id,
                write=True,
            )

            spool = tempfile.SpooledTemporaryFile(
                max_size=min(settings.max_upload_bytes, _SPOOL_MEMORY_LIMIT),
                mode="w+b",
            )
            total_size = 0
            try:
                while True:
                    chunk = await upload_file.read(_READ_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="File exceeds the upload size limit.")
                    await run_in_threadpool(spool.write, chunk)

                if total_size == 0:
                    raise UploadValidationError("Empty files cannot be uploaded.")
                validated = validated.__class__(
                    validated.filename,
                    validated.extension,
                    validated.content_type,
                    total_size,
                )
                return await run_in_threadpool(
                    cls._persist,
                    spool,
                    validated,
                    scope,
                    user_id,
                    provider,
                    db,
                )
            finally:
                await run_in_threadpool(spool.close)
        except UploadValidationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        finally:
            await upload_file.close()

    @classmethod
    def _persist(
        cls,
        spool,
        validated,
        scope: Dict[str, Optional[str]],
        user_id,
        provider: StorageProvider,
        db: Session,
    ) -> UploadResponse:
        try:
            validate_file_content(spool, validated.extension, validated.size)
        except UploadValidationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        metadata: Dict[str, Any] = {
            **{
                key: str(value) if value is not None else None
                for key, value in scope.items()
            },
            "original_filename": validated.filename,
            "content_type": validated.content_type,
            "size": validated.size,
            "created_by_user_id": str(user_id),
        }
        checksum_digest = hashlib.sha256()
        spool.seek(0)
        while True:
            chunk = spool.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            checksum_digest.update(chunk)
        checksum = checksum_digest.hexdigest()
        spool.seek(0)

        for _ in range(_KEY_COLLISION_ATTEMPTS):
            key = uuid.uuid4().hex
            try:
                provider.store(key, spool, metadata)
            except ObjectAlreadyExists:
                continue
            except Exception as exc:
                try:
                    if provider.exists(key):
                        provider.delete(key)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=503, detail="File storage is temporarily unavailable."
                ) from exc
            try:
                from app.services.asset import AssetService

                asset = AssetService.register_uploaded(
                    db,
                    scope=scope,
                    user_id=user_id,
                    storage_key=key,
                    filename=validated.filename,
                    mime_type=validated.content_type,
                    file_size=validated.size,
                    checksum=checksum,
                    storage_provider="local",
                    provider=provider,
                )
            except HTTPException:
                try:
                    provider.delete(key)
                except Exception:
                    pass
                raise
            except Exception as exc:
                try:
                    provider.delete(key)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=503, detail="Could not register the uploaded asset."
                ) from exc
            if asset.processing_status == "pending":
                from app.services.processing_queue import enqueue_asset_processing

                enqueue_asset_processing(asset.id)
            return UploadResponse(
                asset_id=asset.id,
                category=asset.category,
                duplicate_of_asset_id=asset.duplicate_of_asset_id,
                storage_key=key,
                organization_id=scope["organization_id"],
                project_id=scope["project_id"],
                event_id=scope["event_id"],
                original_filename=validated.filename,
                content_type=validated.content_type,
                size=validated.size,
            )
        raise HTTPException(status_code=503, detail="Could not allocate a unique storage reference.")

    @classmethod
    def retrieve(cls, *, db: Session, user_id, storage_key: str, provider: StorageProvider):
        asset = db.scalars(select(Asset).where(Asset.storage_key == storage_key)).first()
        if asset is not None:
            if asset.deleted_at is not None or asset.status != "active":
                raise HTTPException(status_code=404, detail="Stored file not found.")
            from app.services.asset import AssetService

            AssetService.validate_scope(
                db, user_id, asset.organization_id, asset.workspace_id,
                asset.project_id, asset.event_id, write=False,
            )
            try:
                file_stream = provider.open(storage_key)
            except (InvalidStorageKey, ObjectNotFound):
                raise HTTPException(status_code=404, detail="Stored file not found.")
            except StorageError as exc:
                raise HTTPException(status_code=503, detail="File storage is temporarily unavailable.") from exc
            return file_stream, {
                "organization_id": str(asset.organization_id),
                "project_id": str(asset.project_id) if asset.project_id else None,
                "event_id": str(asset.event_id) if asset.event_id else None,
                "original_filename": asset.original_filename,
                "content_type": asset.mime_type,
                "size": asset.file_size,
            }
        try:
            metadata = provider.get_metadata(storage_key)
        except (InvalidStorageKey, ObjectNotFound):
            raise HTTPException(status_code=404, detail="Stored file not found.")
        except StorageError as exc:
            raise HTTPException(status_code=503, detail="File storage is temporarily unavailable.") from exc

        try:
            organization_id = uuid.UUID(metadata["organization_id"])
            project_id = uuid.UUID(metadata["project_id"]) if metadata.get("project_id") else None
            event_id = uuid.UUID(metadata["event_id"]) if metadata.get("event_id") else None
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Stored file not found.") from exc

        cls._validate_scope(
            db, user_id, organization_id, project_id, event_id, write=False
        )
        try:
            file_stream = provider.open(storage_key)
        except (InvalidStorageKey, ObjectNotFound):
            raise HTTPException(status_code=404, detail="Stored file not found.")
        except StorageError as exc:
            raise HTTPException(status_code=503, detail="File storage is temporarily unavailable.") from exc

        return file_stream, metadata
