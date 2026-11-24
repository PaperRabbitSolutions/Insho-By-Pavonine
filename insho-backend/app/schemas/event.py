from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class EventCreate(BaseModel):
    project_id: UUID
    venue_id: Optional[UUID] = None
    event_type_id: Optional[UUID] = None
    name: str = Field(min_length=2, max_length=200)
    description: Optional[str] = None
    event_date: Optional[date] = None
    guest_count: Optional[int] = Field(default=None, ge=0)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    budget: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    status: str = Field(default="draft", pattern="^(draft|scheduled|completed|cancelled)$")

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Schedule timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if self.event_date and self.starts_at and self.event_date != self.starts_at.date():
            raise ValueError("event_date must match the date of starts_at")
        return self


class EventUpdate(BaseModel):
    venue_id: Optional[UUID] = None
    event_type_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    description: Optional[str] = None
    event_date: Optional[date] = None
    guest_count: Optional[int] = Field(default=None, ge=0)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    budget: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    status: Optional[str] = Field(default=None, pattern="^(draft|scheduled|completed|cancelled)$")
    version: Optional[int] = Field(default=None, ge=1)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Schedule timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if self.event_date and self.starts_at and self.event_date != self.starts_at.date():
            raise ValueError("event_date must match the date of starts_at")
        return self


class EventResponse(BaseModel):
    id: UUID
    organization_id: UUID
    event_type_id: Optional[UUID]
    name: str
    description: Optional[str]
    event_date: Optional[date]
    guest_count: Optional[int]
    project_id: Optional[UUID]
    venue_id: Optional[UUID]
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    budget: Optional[Decimal]
    currency: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    version: int

    model_config = ConfigDict(from_attributes=True)
