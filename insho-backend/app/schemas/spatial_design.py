"""Review contracts for Phase 8 design intent on a canonical scene."""

from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.object_library import CatalogValidation, DefinitionSummary
from app.schemas.spatial import ObjectDimensions, SpatialInput, SpatialTransform
from app.schemas.spatial_validation import ValidationReport, ValidationSummary


Module = Literal["mood", "style", "plan", "vision", "elements"]
Kind = Literal["zone_requirement", "element_placement"]
Status = Literal["proposed", "resolved", "needs_resolution", "ready_for_validation", "validated",
                 "applied", "rejected", "stale"]
PositiveRevision = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class Reference(BaseModel):
    asset_id: UUID
    name: str
    role: str


class ArtifactSource(BaseModel):
    artifact_id: UUID
    version: int
    event_id: Optional[UUID]
    source: str
    status: str
    execution_id: Optional[UUID]
    context_stale: bool
    based_on: dict[str, Any]


class ModuleContext(BaseModel):
    module: Module
    status: Literal["available", "partial", "invalid", "missing", "stale", "failed", "running"]
    artifact: Optional[ArtifactSource] = None
    normalized: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    unavailable_reference_count: int = 0
    latest_execution_id: Optional[UUID] = None
    latest_execution_status: Optional[str] = None
    error_code: Optional[str] = None


class IntegrationContext(BaseModel):
    scene_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    scene_revision: int
    context_version: str
    status: Literal["available", "partial", "unavailable"]
    modules: list[ModuleContext]


class RecommendationSource(BaseModel):
    module: Literal["plan", "elements"]
    artifact_id: UUID
    artifact_version: int
    event_id: Optional[UUID]
    execution_id: Optional[UUID]
    path: str
    element_id: Optional[UUID] = None


class ZoneMatch(BaseModel):
    id: UUID
    name: str


class CatalogMatch(BaseModel):
    definition: DefinitionSummary
    reasons: list[Literal["name", "category", "object_type", "style_tags"]]


class RecommendationReview(BaseModel):
    review_id: UUID
    status: Literal["validated", "applied", "rejected"]
    base_revision: int
    context_version: str
    reviewed_at: datetime
    reviewed_by_user_id: Optional[UUID]
    action: Optional["Action"] = None
    validation_summary: Optional[ValidationSummary] = None
    requires_warning_acceptance: bool = False
    receipt: Optional["ApplicationReceipt"] = None
    rejection_reason: Optional[str] = None


class Recommendation(BaseModel):
    id: UUID
    kind: Kind
    status: Status
    title: str
    description: str
    source: RecommendationSource
    intent: dict[str, Any] = Field(default_factory=dict)
    zone_matches: list[ZoneMatch] = Field(default_factory=list)
    candidates: list[CatalogMatch] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    review: Optional[RecommendationReview] = None


class RecommendationPage(BaseModel):
    scene_revision: int
    context_version: str
    items: list[Recommendation]
    total: int
    offset: int
    limit: int


class SnapshotInput(SpatialInput):
    base_revision: PositiveRevision
    context_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class PlacementInput(SpatialInput):
    transform: SpatialTransform

    @model_validator(mode="after")
    def require_coordinates(self):
        if self.transform.position.model_fields_set != {"x", "y", "z"}:
            raise ValueError("An explicit x, y and z position in meters is required")
        return self


class ZoneAction(PlacementInput):
    kind: Literal["create_zone"]
    dimensions: ObjectDimensions


class ObjectAction(PlacementInput):
    kind: Literal["place_object"]
    definition_id: UUID
    definition_version: PositiveRevision
    zone_id: Optional[UUID] = None
    layer_id: Optional[UUID] = None


Action = Annotated[Union[ZoneAction, ObjectAction], Field(discriminator="kind")]


class ValidateRecommendation(SnapshotInput):
    action: Action


class ApplyRecommendation(SpatialInput):
    review_id: UUID
    accept_warnings: Annotated[bool, Field(strict=True)] = False


class RejectRecommendation(SnapshotInput):
    reason: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500,
                                                      pattern=r"^[^\x00-\x1f]+$")]] = None


class RecommendationValidation(BaseModel):
    recommendation_id: UUID
    status: Literal["validated", "validated_with_warnings", "blocked"]
    review_id: Optional[UUID] = None
    base_revision: int
    context_version: str
    proposed_entity_id: UUID
    requires_warning_acceptance: bool
    validation: ValidationReport
    catalog_validation: Optional[CatalogValidation] = None


class ApplicationReceipt(BaseModel):
    recommendation_id: UUID
    review_id: UUID
    status: Literal["applied"] = "applied"
    scene_id: UUID
    scene_revision: int
    entity_id: UUID
    entity_type: Literal["object", "zone"]
    applied_at: datetime
    applied_by_user_id: Optional[UUID]


RecommendationReview.model_rebuild()
