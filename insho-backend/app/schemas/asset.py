from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


AssetCategory = Literal[
    "image", "video", "document", "design", "floor_plan",
    "venue_reference", "inspiration", "audio", "other",
]


class AssetResponse(BaseModel):
    id: UUID
    organization_id: UUID
    workspace_id: Optional[UUID]
    project_id: Optional[UUID]
    event_id: Optional[UUID]
    folder_id: Optional[UUID]
    original_filename: str
    display_name: str
    description: Optional[str]
    category: str
    mime_type: str
    file_extension: str
    file_size: int
    checksum_sha256: Optional[str]
    duplicate_of_asset_id: Optional[UUID]
    status: str
    processing_status: str
    processing_attempts: int
    processing_error_code: Optional[str]
    processing_started_at: Optional[datetime]
    processing_completed_at: Optional[datetime]
    media_metadata: Optional[dict]
    uploaded_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AssetUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    category: Optional[AssetCategory] = None

    model_config = ConfigDict(extra="forbid")


class AssetMove(BaseModel):
    workspace_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    event_id: Optional[UUID] = None
    folder_id: Optional[UUID] = None

    model_config = ConfigDict(extra="forbid")


class AssetFolderCreate(BaseModel):
    organization_id: UUID
    workspace_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    parent_folder_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid")


class AssetFolderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    parent_folder_id: Optional[UUID] = None

    model_config = ConfigDict(extra="forbid")


class AssetFolderResponse(BaseModel):
    id: UUID
    organization_id: UUID
    workspace_id: Optional[UUID]
    project_id: Optional[UUID]
    parent_folder_id: Optional[UUID]
    name: str
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetDerivativeResponse(BaseModel):
    id: UUID
    asset_id: UUID
    kind: str
    mime_type: str
    file_extension: str
    file_size: int
    checksum_sha256: str
    width: Optional[int]
    height: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StorageUsageResponse(BaseModel):
    organization_id: UUID
    project_id: Optional[UUID]
    asset_count: int
    original_bytes: int
    derivative_bytes: int
    total_bytes: int


class AssetProcessingResponse(BaseModel):
    asset_id: UUID
    status: str
    attempts: int
    error_code: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    metadata: Optional[dict]
    derivatives: list[AssetDerivativeResponse]
