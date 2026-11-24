from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VenueZoneCreate(BaseModel):
    venue_id: UUID
    name: str = Field(min_length=2, max_length=150)
    area: Optional[Decimal] = Field(default=None, gt=0)
    description: Optional[str] = None


class VenueZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    area: Optional[Decimal] = Field(default=None, gt=0)
    description: Optional[str] = None


class VenueZoneResponse(BaseModel):
    id: UUID
    venue_id: UUID
    name: str
    area: Optional[Decimal]
    description: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
