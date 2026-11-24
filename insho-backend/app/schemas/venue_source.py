from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VenueSourceCreate(BaseModel):
    venue_id: UUID
    source_type: str = Field(min_length=1, max_length=50)
    file_url: Optional[str] = None
    source_metadata: Optional[Dict[str, Any]] = None


class VenueSourceUpdate(BaseModel):
    source_type: Optional[str] = Field(default=None, min_length=1, max_length=50)
    file_url: Optional[str] = None
    source_metadata: Optional[Dict[str, Any]] = None


class VenueSourceResponse(BaseModel):
    id: UUID
    venue_id: UUID
    source_type: str
    file_url: Optional[str]
    source_metadata: Optional[Dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
