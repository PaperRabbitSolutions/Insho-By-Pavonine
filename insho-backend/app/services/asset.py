from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.asset_folder import AssetFolder
from app.models.asset_derivative import AssetDerivative
from app.models.workspace import Workspace
from app.schemas.asset import AssetFolderCreate, AssetFolderUpdate, AssetMove, AssetUpdate
from app.security import require_event_access, require_membership, require_project_access


ASSET_CATEGORIES = {
    "image", "video", "document", "design", "floor_plan",
    "venue_reference", "inspiration", "audio", "other",
}
_CATEGORY_BY_MIME = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "application/pdf": "document",
    "image/svg+xml": "design",
}


def _not_found():
    raise HTTPException(status_code=404, detail="Resource not found")


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 120 or any(ord(ch) < 32 for ch in cleaned):
        raise HTTPException(status_code=422, detail="Folder name is invalid")
    return cleaned


class AssetService:
    @staticmethod
    def validate_scope(
        db: Session,
        user_id: UUID,
        organization_id: UUID,
        workspace_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        event_id: Optional[UUID] = None,
        *,
        write: bool = False,
    ):
        require_membership(db, user_id, organization_id, write=write)
        requested_workspace_id = workspace_id
        workspace = None
        project = None
        event = None
        if workspace_id is not None:
            workspace = db.get(Workspace, workspace_id)
            if not workspace or workspace.organization_id != organization_id:
                _not_found()
        if project_id is not None:
            project = require_project_access(db, user_id, project_id, write=write)
            if project.organization_id != organization_id:
                _not_found()
            if workspace is not None and project.workspace_id != workspace.id:
                _not_found()
            workspace_id = project.workspace_id
            workspace = db.get(Workspace, workspace_id)
            if not workspace or workspace.organization_id != organization_id:
                _not_found()
        if event_id is not None:
            event = require_event_access(db, user_id, event_id, write=write)
            if event.organization_id != organization_id:
                _not_found()
            if event.project_id is not None:
                event_project = require_project_access(
                    db, user_id, event.project_id, write=write
                )
                if event_project.organization_id != organization_id:
                    _not_found()
                if project is not None and project.id != event_project.id:
                    _not_found()
                if (
                    requested_workspace_id is not None
                    and requested_workspace_id != event_project.workspace_id
                ):
                    _not_found()
                project = event_project
                workspace_id = event_project.workspace_id
                workspace = db.get(Workspace, workspace_id)
            if workspace is not None and workspace.organization_id != organization_id:
                _not_found()
        return {
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "project_id": project.id if project else None,
            "event_id": event.id if event else None,
        }

    @staticmethod
    def _validate_folder(db: Session, user_id: UUID, scope: dict, folder_id: Optional[UUID], *, write: bool):
        if folder_id is None:
            return None
        folder = db.get(AssetFolder, folder_id)
        if not folder or folder.deleted_at is not None:
            _not_found()
        require_membership(db, user_id, folder.organization_id, write=write)
        if (
            folder.organization_id != scope["organization_id"]
            or folder.workspace_id != scope["workspace_id"]
            or (folder.project_id is not None and folder.project_id != scope["project_id"])
        ):
            _not_found()
        return folder

    @classmethod
    def register_uploaded(
        cls, db: Session, *, scope: dict, user_id: UUID, storage_key: str,
        filename: str, mime_type: str, file_size: int, checksum: str,
        storage_provider: str, provider,
    ) -> Asset:
        if not provider.exists(storage_key):
            raise HTTPException(status_code=503, detail="Stored file is unavailable.")
        validated_scope = cls.validate_scope(
            db, user_id, scope["organization_id"], scope.get("workspace_id"),
            scope.get("project_id"), scope.get("event_id"), write=True,
        )
        extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        duplicate = db.scalars(
            select(Asset).where(
                Asset.organization_id == validated_scope["organization_id"],
                Asset.checksum_sha256 == checksum,
                Asset.deleted_at.is_(None),
            ).order_by(Asset.created_at.asc()).limit(1)
        ).first()
        processing_status = (
            "pending"
            if mime_type in {"image/jpeg", "image/png", "image/webp", "video/mp4"}
            else "not_applicable"
        )
        asset = Asset(
            **validated_scope,
            original_filename=filename,
            display_name=filename,
            storage_key=storage_key,
            storage_provider=storage_provider,
            mime_type=mime_type,
            file_extension=extension,
            file_size=file_size,
            checksum_sha256=checksum,
            duplicate_of_asset_id=duplicate.id if duplicate else None,
            category=_CATEGORY_BY_MIME.get(mime_type, "other"),
            processing_status=processing_status,
            uploaded_by_user_id=user_id,
        )
        try:
            db.add(asset)
            db.commit()
            db.refresh(asset)
        except Exception:
            db.rollback()
            raise
        return asset

    @staticmethod
    def get(db: Session, asset_id: UUID, user_id: UUID, *, write: bool = False, include_deleted: bool = False,
            read_lock: bool = False):
        asset = db.get(Asset, asset_id, with_for_update={"read": True}, populate_existing=True) if read_lock else db.get(Asset, asset_id)
        if not asset or (asset.deleted_at is not None and not include_deleted):
            _not_found()
        AssetService.validate_scope(
            db, user_id, asset.organization_id, asset.workspace_id,
            asset.project_id, asset.event_id, write=write,
        )
        return asset

    @classmethod
    def list_assets(
        cls, db: Session, user_id: UUID, organization_id: UUID, *,
        workspace_id: Optional[UUID] = None, project_id: Optional[UUID] = None,
        event_id: Optional[UUID] = None, folder_id: Optional[UUID] = None,
        category: Optional[str] = None, mime_type: Optional[str] = None,
        extension: Optional[str] = None, status: str = "active",
        q: Optional[str] = None, created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None, sort_by: str = "created_at",
        sort_order: str = "desc", offset: int = 0, limit: int = 50,
    ):
        if status not in {"active", "deleted", "all"}:
            raise HTTPException(status_code=422, detail="Invalid asset status filter")
        if category and category not in ASSET_CATEGORIES:
            raise HTTPException(status_code=422, detail="Invalid asset category")
        scope = cls.validate_scope(
            db, user_id, organization_id, workspace_id, project_id, event_id
        )
        stmt = select(Asset).where(Asset.organization_id == organization_id)
        if project_id is not None:
            stmt = stmt.where(Asset.project_id == scope["project_id"])
        if event_id is not None:
            stmt = stmt.where(Asset.event_id == scope["event_id"])
        if workspace_id is not None:
            stmt = stmt.where(Asset.workspace_id == scope["workspace_id"])
        if folder_id is not None:
            cls.get_folder(db, folder_id, user_id)
            stmt = stmt.where(Asset.folder_id == folder_id)
        if status == "active":
            stmt = stmt.where(Asset.deleted_at.is_(None))
        elif status == "deleted":
            stmt = stmt.where(Asset.deleted_at.is_not(None))
        if category:
            stmt = stmt.where(Asset.category == category)
        if mime_type:
            stmt = stmt.where(Asset.mime_type == mime_type.strip().lower())
        if extension:
            normalized_extension = extension.lower()
            if not normalized_extension.startswith("."):
                normalized_extension = "." + normalized_extension
            stmt = stmt.where(Asset.file_extension == normalized_extension)
        if q:
            safe_query = q[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{safe_query}%"
            stmt = stmt.where(or_(
                Asset.original_filename.ilike(pattern, escape="\\"),
                Asset.display_name.ilike(pattern, escape="\\"),
            ))
        if created_from:
            stmt = stmt.where(Asset.created_at >= created_from)
        if created_to:
            stmt = stmt.where(Asset.created_at <= created_to)
        sort_columns = {
            "created_at": Asset.created_at,
            "updated_at": Asset.updated_at,
            "name": Asset.display_name,
            "file_size": Asset.file_size,
        }
        column = sort_columns[sort_by]
        order = column.asc() if sort_order == "asc" else column.desc()
        return db.scalars(stmt.order_by(order, Asset.id.asc()).offset(offset).limit(limit)).all()

    @staticmethod
    def update(db: Session, asset_id: UUID, user_id: UUID, data: AssetUpdate):
        asset = AssetService.get(db, asset_id, user_id, write=True)
        values = data.model_dump(exclude_unset=True)
        for field, value in values.items():
            if value is None and field in {"display_name", "category"}:
                raise HTTPException(status_code=422, detail=f"{field} cannot be null")
            if field == "display_name":
                clean = value.strip()
                if not clean or len(clean) > 255:
                    raise HTTPException(status_code=422, detail="Display name is invalid")
                values[field] = clean
        for field, value in values.items():
            setattr(asset, field, value)
        return AssetService._commit(db, asset)

    @classmethod
    def move(cls, db: Session, asset_id: UUID, user_id: UUID, data: AssetMove):
        asset = cls.get(db, asset_id, user_id, write=True)
        changes = data.model_dump(exclude_unset=True)
        scope = cls.validate_scope(
            db, user_id, asset.organization_id,
            changes.get("workspace_id", asset.workspace_id),
            changes.get("project_id", asset.project_id),
            changes.get("event_id", asset.event_id), write=True,
        )
        folder_id = changes.get("folder_id", asset.folder_id)
        cls._validate_folder(db, user_id, scope, folder_id, write=True)
        for field, value in scope.items():
            setattr(asset, field, value)
        asset.folder_id = folder_id
        return cls._commit(db, asset)

    @staticmethod
    def delete(db: Session, asset_id: UUID, user_id: UUID):
        asset = AssetService.get(db, asset_id, user_id, write=True)
        asset.status = "deleted"
        asset.deleted_at = datetime.now(timezone.utc)
        return AssetService._commit(db, asset)

    @staticmethod
    def restore(db: Session, asset_id: UUID, user_id: UUID):
        asset = db.scalars(select(Asset).where(Asset.id == asset_id).with_for_update()).first()
        if asset is None:
            _not_found()
        AssetService.validate_scope(
            db, user_id, asset.organization_id, asset.workspace_id,
            asset.project_id, asset.event_id, write=True,
        )
        if asset.deleted_at is None:
            return asset
        AssetService.validate_scope(
            db, user_id, asset.organization_id, asset.workspace_id,
            asset.project_id, asset.event_id, write=True,
        )
        AssetService._validate_folder(db, user_id, {
            "organization_id": asset.organization_id,
            "workspace_id": asset.workspace_id,
            "project_id": asset.project_id,
        }, asset.folder_id, write=True)
        asset.status = "active"
        asset.deleted_at = None
        return AssetService._commit(db, asset)

    @staticmethod
    def _commit(db: Session, record):
        try:
            db.commit()
            db.refresh(record)
        except Exception:
            db.rollback()
            raise
        return record

    @staticmethod
    def _validate_folder_scope(db: Session, user_id: UUID, organization_id, workspace_id, project_id, *, write: bool):
        return AssetService.validate_scope(
            db, user_id, organization_id, workspace_id, project_id, write=write
        )

    @staticmethod
    def storage_usage(db: Session, user_id: UUID, organization_id: UUID, project_id=None):
        scope = AssetService.validate_scope(
            db, user_id, organization_id, project_id=project_id, write=False
        )
        filters = [Asset.organization_id == organization_id, Asset.status == "active", Asset.deleted_at.is_(None)]
        derivative_filters = [
            Asset.organization_id == organization_id,
            Asset.status == "active",
            Asset.deleted_at.is_(None),
        ]
        if project_id is not None:
            filters.append(Asset.project_id == scope["project_id"])
            derivative_filters.append(Asset.project_id == scope["project_id"])
        count, original_bytes = db.execute(
            select(func.count(Asset.id), func.coalesce(func.sum(Asset.file_size), 0)).where(*filters)
        ).one()
        derivative_bytes = db.scalar(
            select(func.coalesce(func.sum(AssetDerivative.file_size), 0))
            .join(Asset, AssetDerivative.asset_id == Asset.id)
            .where(*derivative_filters)
        ) or 0
        return {
            "organization_id": organization_id,
            "project_id": scope["project_id"],
            "asset_count": int(count),
            "original_bytes": int(original_bytes),
            "derivative_bytes": int(derivative_bytes),
            "total_bytes": int(original_bytes) + int(derivative_bytes),
        }

    @staticmethod
    def derivative(db: Session, asset_id: UUID, user_id: UUID, kind: str):
        asset = AssetService.get(db, asset_id, user_id)
        derivative = db.scalars(
            select(AssetDerivative).where(
                AssetDerivative.asset_id == asset.id,
                AssetDerivative.kind == kind,
            )
        ).first()
        if derivative is None:
            if asset.processing_status in {"pending", "processing"}:
                raise HTTPException(status_code=409, detail="Asset preview is still processing")
            raise HTTPException(status_code=404, detail="Asset preview is unavailable")
        return derivative

    @staticmethod
    def retry_processing(db: Session, asset_id: UUID, user_id: UUID):
        asset = db.scalars(select(Asset).where(Asset.id == asset_id).with_for_update()).first()
        if asset is None or asset.deleted_at is not None:
            _not_found()
        AssetService.validate_scope(
            db, user_id, asset.organization_id, asset.workspace_id,
            asset.project_id, asset.event_id, write=True,
        )
        stale_processing = (
            asset.processing_status == "processing"
            and asset.processing_started_at is not None
            and asset.processing_started_at < datetime.now(timezone.utc) - timedelta(minutes=10)
        )
        if asset.processing_status not in {"failed", "not_started"} and not stale_processing:
            raise HTTPException(status_code=409, detail="Asset processing cannot be retried in its current state")
        if asset.mime_type not in {"image/jpeg", "image/png", "image/webp", "video/mp4"}:
            raise HTTPException(status_code=422, detail="This asset type is not processable")
        asset.processing_status = "pending"
        asset.processing_error_code = None
        asset.processing_completed_at = None
        AssetService._commit(db, asset)
        from app.services.processing_queue import enqueue_asset_processing

        enqueue_asset_processing(asset.id)
        db.refresh(asset)
        return asset

    @classmethod
    def create_folder(cls, db: Session, user_id: UUID, data: AssetFolderCreate):
        scope = cls._validate_folder_scope(
            db, user_id, data.organization_id, data.workspace_id, data.project_id, write=True
        )
        parent_id = data.parent_folder_id
        if parent_id:
            parent = cls.get_folder(db, parent_id, user_id, write=True)
            if any(
                getattr(parent, key) != scope[key]
                for key in ("organization_id", "workspace_id", "project_id")
            ):
                _not_found()
        name = _clean_name(data.name)
        cls._assert_unique_folder_name(db, scope, parent_id, name)
        folder_scope = {
            key: scope[key]
            for key in ("organization_id", "workspace_id", "project_id")
        }
        folder = AssetFolder(
            **folder_scope,
            parent_folder_id=parent_id,
            name=name,
            created_by_user_id=user_id,
        )
        db.add(folder)
        return cls._commit_folder(db, folder)

    @classmethod
    def list_folders(cls, db: Session, user_id: UUID, organization_id: UUID, *, workspace_id=None, project_id=None):
        scope = cls._validate_folder_scope(
            db, user_id, organization_id, workspace_id, project_id, write=False
        )
        stmt = select(AssetFolder).where(
            AssetFolder.organization_id == organization_id,
            AssetFolder.deleted_at.is_(None),
        )
        if workspace_id is not None:
            stmt = stmt.where(AssetFolder.workspace_id == scope["workspace_id"])
        if project_id is not None:
            stmt = stmt.where(AssetFolder.project_id == scope["project_id"])
        return db.scalars(stmt.order_by(AssetFolder.name.asc(), AssetFolder.id.asc())).all()

    @staticmethod
    def get_folder(db: Session, folder_id: UUID, user_id: UUID, *, write: bool = False, include_deleted: bool = False):
        folder = db.get(AssetFolder, folder_id)
        if not folder or (folder.deleted_at is not None and not include_deleted):
            _not_found()
        AssetService._validate_folder_scope(
            db, user_id, folder.organization_id, folder.workspace_id,
            folder.project_id, write=write,
        )
        return folder

    @classmethod
    def update_folder(cls, db: Session, folder_id: UUID, user_id: UUID, data: AssetFolderUpdate):
        folder = cls.get_folder(db, folder_id, user_id, write=True)
        values = data.model_dump(exclude_unset=True)
        if "name" in values:
            if values["name"] is None:
                raise HTTPException(status_code=422, detail="Folder name cannot be null")
            values["name"] = _clean_name(values["name"])
        parent_id = values.get("parent_folder_id", folder.parent_folder_id)
        if parent_id == folder.id:
            raise HTTPException(status_code=422, detail="A folder cannot be its own parent")
        if parent_id is not None:
            parent = cls.get_folder(db, parent_id, user_id, write=True)
            if any(getattr(parent, key) != getattr(folder, key) for key in ("organization_id", "workspace_id", "project_id")):
                _not_found()
            visited = set()
            cursor = parent
            while cursor is not None:
                if cursor.id == folder.id or cursor.id in visited:
                    raise HTTPException(status_code=422, detail="Folder move would create a cycle")
                visited.add(cursor.id)
                cursor = db.get(AssetFolder, cursor.parent_folder_id) if cursor.parent_folder_id else None
        new_name = values.get("name", folder.name)
        cls._assert_unique_folder_name(
            db,
            {key: getattr(folder, key) for key in ("organization_id", "workspace_id", "project_id")},
            parent_id,
            new_name,
            excluding_id=folder.id,
        )
        folder.name = new_name
        folder.parent_folder_id = parent_id
        return cls._commit_folder(db, folder)

    @classmethod
    def delete_folder(cls, db: Session, folder_id: UUID, user_id: UUID):
        folder = cls.get_folder(db, folder_id, user_id, write=True)
        has_children = db.scalar(select(AssetFolder.id).where(
            AssetFolder.parent_folder_id == folder.id,
        ).limit(1))
        has_assets = db.scalar(select(Asset.id).where(Asset.folder_id == folder.id).limit(1))
        if has_children or has_assets:
            raise HTTPException(status_code=409, detail="Folder must be empty before it can be deleted")
        folder.deleted_at = datetime.now(timezone.utc)
        return cls._commit(db, folder)

    @staticmethod
    def _assert_unique_folder_name(db: Session, scope: dict, parent_id, name: str, excluding_id=None):
        query = select(AssetFolder.id).where(
            AssetFolder.organization_id == scope["organization_id"],
            AssetFolder.workspace_id == scope["workspace_id"],
            AssetFolder.project_id == scope["project_id"],
            AssetFolder.parent_folder_id == parent_id,
            AssetFolder.deleted_at.is_(None),
            func.lower(AssetFolder.name) == name.lower(),
        )
        if excluding_id:
            query = query.where(AssetFolder.id != excluding_id)
        if db.scalar(query.limit(1)):
            raise HTTPException(status_code=409, detail="A folder with this name already exists here")

    @staticmethod
    def _commit_folder(db: Session, folder: AssetFolder):
        try:
            db.commit()
            db.refresh(folder)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="A folder with this name already exists here") from exc
        except Exception:
            db.rollback()
            raise
        return folder
