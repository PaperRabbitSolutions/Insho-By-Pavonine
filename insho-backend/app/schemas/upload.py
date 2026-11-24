from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class UploadResponse(BaseModel):
    asset_id: Optional[UUID] = None
    category: Optional[str] = None
    duplicate_of_asset_id: Optional[UUID] = None
    storage_key: str
    organization_id: UUID
    project_id: Optional[UUID]
    event_id: Optional[UUID]
    original_filename: str
    content_type: str
    size: int
