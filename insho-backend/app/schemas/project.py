from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class ProjectCreate(BaseModel):
    organization_id: UUID
    workspace_id: UUID
    name: str = Field(min_length=2, max_length=200)
    description: Optional[str] = None
    status: str = Field(default="draft", pattern="^(draft|active|completed|archived)$")
    budget: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    guest_count: Optional[int] = Field(default=None, ge=0)
    event_dates: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    description: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(draft|active|completed|archived)$")
    budget: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    version: Optional[int] = Field(default=None, ge=1)
    guest_count: Optional[int] = Field(default=None, ge=0)
    event_dates: Optional[Dict[str, Any]] = None
    constraints: Optional[Dict[str, Any]] = None


class ProjectResponse(BaseModel):
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    name: str
    description: Optional[str]
    status: str
    budget: Optional[Decimal]
    guest_count: Optional[int]
    event_dates: Optional[Dict[str, Any]]
    constraints: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    version: int

    model_config = ConfigDict(from_attributes=True)
