from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.production import Version
from app.schemas.spatial import SpatialInput


class ProjectMemoryCreate(BaseModel):
    project_id: UUID
    memory_data: Dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = None


class ProjectMemoryUpdate(BaseModel):
    memory_data: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None


class ProjectMemoryResponse(BaseModel):
    id: UUID
    project_id: UUID
    memory_data: Dict[str, Any]
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


MemoryCategory = Literal[
    "must_have", "must_avoid", "approved", "rejected", "client_preferences", "design_dna", "budget", "venue_dimensions",
    "guest_count", "previous_decisions", "vendor_constraints", "production_constraints", "safety_constraints",
]
MemoryKind = Literal["decision", "preference", "constraint", "requirement", "fact", "note"]
Verification = Literal["unverified", "verified"]


class MemoryEdit(SpatialInput):
    @field_validator("title", "source_type", "source_reference", "reason", check_fields=False)
    @classmethod
    def safe_text(cls, value):
        return cls.valid_label(value).strip() if value is not None else None

    @field_validator("content", check_fields=False)
    @classmethod
    def require_structured_content(cls, value):
        if value is not None and not value:
            raise ValueError("content must contain at least one structured field")
        return cls.valid_storage_json(value)


class ProjectMemoryItemCreate(MemoryEdit):
    project_id: UUID
    event_id: Optional[UUID] = None
    kind: Optional[MemoryKind] = None
    title: str = Field(min_length=1, max_length=200)
    content: Dict[str, Any]
    source_type: str = Field(default="user", min_length=1, max_length=40)
    source_reference: Optional[str] = Field(default=None, max_length=4000)
    category: Optional[MemoryCategory] = None
    description: Optional[str] = Field(default=None, max_length=4000)
    verification: Verification = "unverified"
    linked_version_id: Optional[UUID] = None
    decision_id: Optional[UUID] = None
    execution_id: Optional[UUID] = None
    reason: Optional[str] = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def classify_memory(self):
        if self.kind is None and self.category is None:
            raise ValueError("Supply a memory category or existing item kind.")
        return self


class ProjectMemoryItemUpdate(MemoryEdit):
    event_id: Optional[UUID] = None
    kind: Optional[MemoryKind] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    content: Optional[Dict[str, Any]] = None
    source_type: Optional[str] = Field(default=None, min_length=1, max_length=40)
    source_reference: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[str] = Field(default=None, pattern="^(active|archived)$")
    expected_version: Optional[Version] = None
    category: Optional[MemoryCategory] = None
    description: Optional[str] = Field(default=None, max_length=4000)
    verification: Optional[Verification] = None
    linked_version_id: Optional[UUID] = None
    decision_id: Optional[UUID] = None
    reason: Optional[str] = Field(default=None, max_length=4000)


class MemoryArchive(MemoryEdit):
    expected_version: Version
    reason: str = Field(min_length=1, max_length=4000)


class ContextMemoryItem(BaseModel):
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    kind: str
    title: str
    content: Dict[str, Any]
    source_type: str
    source_reference: Optional[str]
    created_by_user_id: Optional[UUID]
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class ProjectMemoryItemResponse(ContextMemoryItem):
    category: Optional[MemoryCategory]
    description: Optional[str]
    verification: Verification
    version: int
    linked_version_id: Optional[UUID]
    decision_id: Optional[UUID]
    execution_id: Optional[UUID]


class MemoryRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    item_id: UUID
    version: int
    state: dict
    action: str
    reason: Optional[str]
    changed_by_user_id: Optional[UUID]
    created_at: datetime
