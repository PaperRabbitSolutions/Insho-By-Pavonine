"""Revision-scoped design audit request and evidence contracts."""

from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Dimension = Literal[
    "creativity", "originality", "theme_consistency", "color_harmony", "floral_quality", "furniture_decor", "lighting",
    "scale_proportion", "spatial_feasibility", "production_complexity", "budget_efficiency", "photography_social_impact",
    "guest_experience", "accessibility", "sustainability", "led_suitability", "technical_completeness", "risk_safety_flags",
]
AuditStatus = Literal["running", "completed", "completed_with_warnings", "failed", "cancelled", "interrupted"]
Severity = Literal["info", "review", "high"]
Confidence = Literal["high", "medium", "low", "inconclusive"]
Origin = Literal["observed", "derived", "ai_inferred"]
SourceKind = Literal["design_version", "project", "event", "venue", "scene", "scene_object", "scene_zone", "scene_layer",
                     "design_dna", "design_artifact", "design_element", "presentation_revision", "approval_checkpoint", "asset",
                     "bom", "bom_line", "budget", "technical_specification", "quantity", "production_record", "memory_item"]
ClearanceID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[^\x00-\x1f]+$")]


class AuditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditOptions(AuditInput):
    ai_assisted: Annotated[bool, Field(strict=True)] = False
    accessibility_clearance_ids: list[ClearanceID] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def distinct_clearances(self):
        if len(self.accessibility_clearance_ids) != len(set(self.accessibility_clearance_ids)):
            raise ValueError("Accessibility clearance IDs must be unique.")
        return self


class AuditCreate(AuditOptions):
    design_version_id: UUID


class SourceReference(BaseModel):
    kind: SourceKind
    id: UUID
    revision: Optional[int] = None
    field: Optional[str] = None


class Evidence(BaseModel):
    id: UUID
    origin: Origin
    source: SourceReference
    statement: str
    value: Optional[dict] = None
    # Asset availability is checked at read time. The historical observation remains immutable.
    available: Optional[bool] = None


class Finding(BaseModel):
    code: str
    origin: Origin
    severity: Severity
    explanation: str
    evidence_ids: list[UUID] = Field(min_length=1)
    source_finding_id: Optional[UUID] = None


class Recommendation(BaseModel):
    finding_code: str
    action: str
    rationale: str
    targets: list[SourceReference] = Field(min_length=1)
    affected_dependencies: list[SourceReference] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(min_length=1)
    review_required: Literal["editor", "professional"]


class Evaluator(BaseModel):
    method: Literal["deterministic", "ai_assisted", "preliminary_screening"]
    criterion: str
    execution_id: Optional[UUID] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class DimensionResult(BaseModel):
    dimension: Dimension
    state: Literal["evaluated", "not_evaluated"]
    score: Optional[Literal[1, 2, 3]] = None
    explanation: str
    confidence: Confidence
    uncertainty: list[str]
    missing_inputs: list[str]
    severity: Severity
    evidence: list[Evidence] = Field(min_length=1)
    findings: list[Finding]
    recommendations: list[Recommendation] = Field(min_length=1)
    evaluator: Evaluator

    @model_validator(mode="after")
    def evidence_and_score(self):
        evaluated = self.state == "evaluated"
        if evaluated != (self.score is not None) or evaluated == (self.confidence == "inconclusive"):
            raise ValueError("Score and confidence must match the evaluation state.")
        if self.dimension == "risk_safety_flags" and (evaluated or self.evaluator.method != "preliminary_screening"
                or any(item.review_required != "professional" for item in self.recommendations)):
            raise ValueError("Safety results are preliminary and require professional review.")
        ids = {item.id for item in self.evidence}
        if any(not set(item.evidence_ids) <= ids for item in [*self.findings, *self.recommendations]):
            raise ValueError("Finding and recommendation evidence must belong to the dimension.")
        return self


class AuditWarning(BaseModel):
    code: str
    message: str
    retryable: bool = False
    source: Optional[SourceReference] = None


class AuditSummary(BaseModel):
    evaluated: int
    not_evaluated: int
    high_priority_dimensions: list[Dimension]
    missing_inputs: list[str]


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    design_version_id: UUID
    snapshot_id: UUID
    initiated_by_user_id: Optional[UUID]
    request_id: UUID
    retry_of_id: Optional[UUID]
    status: AuditStatus
    configuration_version: str
    options: AuditOptions
    input_fingerprint: str
    started_at: datetime
    completed_at: Optional[datetime]
    summary: Optional[AuditSummary]
    warnings: list[AuditWarning]
    retryable: bool
    ai_execution_id: Optional[UUID] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class AuditDetail(AuditRead):
    results: list[DimensionResult]
    evidence_sources: list[SourceReference]
    score_scale: dict[int, str]
    safety_scope: str


class ComparisonEntry(BaseModel):
    dimension: Dimension
    from_state: Literal["evaluated", "not_evaluated"]
    to_state: Literal["evaluated", "not_evaluated"]
    from_score: Optional[int]
    to_score: Optional[int]
    from_confidence: Confidence
    to_confidence: Confidence
    comparable: bool
    reason: str
    from_finding_codes: list[str]
    to_finding_codes: list[str]


class AuditComparison(BaseModel):
    from_evaluation_id: UUID
    to_evaluation_id: UUID
    from_design_version_id: UUID
    to_design_version_id: UUID
    dimensions: list[ComparisonEntry]
