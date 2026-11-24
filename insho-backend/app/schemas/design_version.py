from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.presentation import CheckpointStatus, PresentationInput
from app.schemas.production import Version


class PresentationPin(PresentationInput):
    id: UUID
    revision: Version


class DesignSelection(PresentationInput):
    design_dna_id: Optional[UUID] = None
    scene_ids: list[UUID] = Field(default_factory=list, max_length=10)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=30)
    presentation: Optional[PresentationPin] = None
    budget_ids: list[UUID] = Field(default_factory=list, max_length=10)
    bom_ids: list[UUID] = Field(default_factory=list, max_length=10)
    memory_item_ids: list[UUID] = Field(default_factory=list, max_length=50)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @field_validator("scene_ids", "artifact_ids", "budget_ids", "bom_ids", "memory_item_ids", "asset_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Source IDs must be unique.")
        return value


class ChangeNote(PresentationInput):
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason", "summary", check_fields=False)
    @classmethod
    def clean_note(cls, value):
        return cls.valid_label(value).strip()


class VersionNote(ChangeNote):
    summary: str = Field(min_length=1, max_length=200)


class VersionWrite(VersionNote):
    expected_head_id: Optional[UUID]
    # Omitted fields carry forward. Explicit empty lists remove the corresponding selection.
    selection: DesignSelection = Field(default_factory=DesignSelection)
    refresh_context: bool = False


class RestoreVersion(VersionNote):
    source_version_id: UUID
    expected_head_id: UUID


class BranchCreate(ChangeNote):
    source_version_id: UUID
    expected_mainline_id: UUID
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)


class PromoteBranch(VersionNote):
    expected_head_id: UUID
    expected_mainline_id: UUID
    mode: Literal["whole_state"]


class ArchiveBranch(ChangeNote):
    expected_head_id: UUID


class SourceChange(BaseModel):
    kind: str
    id: UUID
    change: Literal["added", "removed", "updated"]
    fields: list[str]
    scene_id: Optional[UUID] = None


class VersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    branch_id: Optional[UUID]
    number: int
    parent_id: Optional[UUID]
    based_on_id: Optional[UUID]
    snapshot_id: UUID
    summary: str
    reason: str
    created_by_user_id: Optional[UUID]
    created_at: datetime
    status: CheckpointStatus = "draft"
    review: Optional[PresentationPin] = None


class VersionDetail(VersionRead):
    state: dict
    unavailable_asset_ids: list[UUID]


class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    source_version_id: UUID
    parent_branch_id: Optional[UUID]
    base_mainline_id: UUID
    name: str
    description: Optional[str]
    status: Literal["active", "promoted", "archived"]
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime
    current_version_id: UUID


class VersionComparison(BaseModel):
    from_version_id: UUID
    to_version_id: UUID
    changes: list[SourceChange]
    affected_source_ids: list[UUID]


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    version_id: Optional[UUID]
    branch_id: Optional[UUID]
    actor_id: Optional[UUID]
    execution_id: Optional[UUID]
    action: str
    entity_type: str
    entity_id: UUID
    reason: Optional[str]
    details: dict
    request_id: UUID
    created_at: datetime
