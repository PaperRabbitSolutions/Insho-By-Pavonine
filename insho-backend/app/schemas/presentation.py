from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.production import Version
from app.schemas.spatial import SpatialInput


Section = Literal[
    "concept_story", "design_dna", "moodboard", "hero_visual", "spatial_plan", "views_3d", "journey",
    "material_palette", "led_visual_preview", "budget_summary", "production_confidence", "alternatives",
]
ClientContext = Literal["luxury", "corporate", "bride_family", "hotel", "agency", "brand", "government"]
Purpose = Literal["concept", "design_review", "production_review"]
SourceKind = Literal["design_dna", "design_artifact", "spatial_scene", "project_memory_item", "material_assignment",
                     "led_quantity", "budget", "production_record", "alternative"]
Applicability = Literal["auto", "required", "optional", "not_applicable"]
Requirement = Literal["required", "optional", "not_applicable"]
CheckpointStatus = Literal["draft", "pending", "approved", "rejected", "revision_requested"]
Decision = Literal["approved", "rejected", "revision_requested"]

Title = Annotated[str, Field(min_length=1, max_length=160)]
Note = Annotated[str, Field(min_length=1, max_length=4000)]


class PresentationInput(SpatialInput):
    @field_validator("title", "text", "comment", "feedback", check_fields=False)
    @classmethod
    def clean_text(cls, value):
        return cls.valid_label(value).strip() if value is not None else None


class SourceLink(PresentationInput):
    kind: SourceKind
    id: UUID
    version: Optional[Version] = None

    @model_validator(mode="after")
    def validate_alternative(self):
        if (self.version is not None) != (self.kind == "alternative"):
            raise ValueError("Specify a version for an alternative revision only.")
        return self


class SectionContent(PresentationInput):
    text: Optional[Note] = None
    applicability: Applicability = "auto"
    sources: list[SourceLink] = Field(default_factory=list, max_length=8)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=15)

    @model_validator(mode="after")
    def validate_content(self):
        if self.applicability == "not_applicable" and (self.text or self.sources or self.asset_ids):
            raise ValueError("An inapplicable section must be empty.")
        if len({(s.kind, s.id) for s in self.sources}) != len(self.sources) or len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("Section references must be unique.")
        return self


class SheetInput(PresentationInput):
    title: Title
    description: Optional[Note] = None
    purpose: Purpose = "design_review"
    client_context: ClientContext
    sections: dict[Section, SectionContent] = Field(default_factory=dict, max_length=12)


class PresentationCreate(SheetInput):
    event_id: Optional[UUID] = None
    alternative_of_id: Optional[UUID] = None


class PresentationUpdate(SheetInput):
    expected_revision: Version


class CheckpointCreate(PresentationInput):
    revision: Version
    title: Title


class ApprovalRequest(PresentationInput):
    revision: Version
    comment: Optional[Note] = None


class ApprovalDecision(PresentationInput):
    revision: Version
    decision: Decision
    feedback: Optional[Note] = None


class CheckpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    presentation_id: UUID
    revision: int
    title: str
    status: CheckpointStatus
    created_by_user_id: Optional[UUID]
    created_at: datetime
    requested_by_user_id: Optional[UUID]
    requested_at: Optional[datetime]
    request_comment: Optional[str]
    decided_by_user_id: Optional[UUID]
    decided_at: Optional[datetime]
    feedback: Optional[str]


class SectionIssue(BaseModel):
    code: str
    message: str
    blocking: bool
    reference_id: Optional[UUID] = None


class SectionValidation(BaseModel):
    section: Section
    requirement: Requirement
    status: Literal["complete", "missing", "unavailable", "not_applicable"]
    issues: list[SectionIssue]


class PresentationValidation(BaseModel):
    revision: int
    complete: bool
    sections: list[SectionValidation]


class EvidenceRead(BaseModel):
    kind: SourceKind
    id: UUID
    version: Optional[int] = None
    status: Literal["current", "changed", "unavailable"]
    data: Optional[dict] = None


class LinkedAssetRead(BaseModel):
    id: UUID
    status: Literal["current", "changed", "unavailable"]
    role: Literal["selected", "reference", "generated"]
    artifact_id: Optional[UUID] = None
    metadata: Optional[dict] = None


class SectionRead(SectionValidation):
    text: Optional[str]
    sources: list[EvidenceRead]
    assets: list[LinkedAssetRead]


class PresentationSummary(BaseModel):
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    alternative_of_id: Optional[UUID]
    revision: int
    current_revision: int
    title: str
    description: Optional[str]
    purpose: Purpose
    client_context: ClientContext
    approval_status: CheckpointStatus
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_by_user_id: Optional[UUID]
    updated_at: datetime


class PresentationRead(PresentationSummary):
    sheet: SheetInput
    section_order: list[Section]
    emphasis: list[Section]
    validation: PresentationValidation
    sections: list[SectionRead]
