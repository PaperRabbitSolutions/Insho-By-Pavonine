from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VenueCreate(BaseModel):
    organization_id: UUID
    name: str = Field(min_length=2, max_length=200)
    address: Optional[str] = None
    city: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, max_length=100)
    total_area: Optional[Decimal] = Field(default=None, gt=0)


class VenueUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    address: Optional[str] = None
    city: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, max_length=100)
    total_area: Optional[Decimal] = Field(default=None, gt=0)


class VenueResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    address: Optional[str]
    city: Optional[str]
    country: Optional[str]
    total_area: Optional[Decimal]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
