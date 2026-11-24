"""Public contracts for read-only, revision-aware spatial validation."""

from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator


CONFIG_KEY = "spatial_validation"
Severity = Literal["error", "warning", "info"]
RuleId = Literal["geometry", "transforms", "hierarchy", "venue_boundary", "zone_containment",
                 "zone_overlap", "collision", "clearance", "consistency", "object_library"]
Category = Literal["geometry", "transform", "hierarchy", "containment", "collision", "clearance",
                   "consistency", "library"]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80,
                                        pattern=r"^[^\x00-\x1f]+$")]
Revision = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class ValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleOptions(ValidationInput):
    enabled: Optional[Annotated[bool, Field(strict=True)]] = None
    severity: Optional[Severity] = None


class EntityPair(ValidationInput):
    first_id: UUID
    second_id: UUID

    @model_validator(mode="after")
    def distinct_ids(self):
        if self.first_id == self.second_id:
            raise ValueError("Pair members must be different entities")
        return self

    def key(self):
        return tuple(sorted((self.first_id, self.second_id)))


class CollisionOptions(ValidationInput):
    include_hidden: Optional[Annotated[bool, Field(strict=True)]] = None
    ignore_ancestor_pairs: Optional[Annotated[bool, Field(strict=True)]] = None
    report_touching: Optional[Annotated[bool, Field(strict=True)]] = None
    allowed_pairs: Optional[list[EntityPair]] = Field(default=None, max_length=500)


class ClearanceSelector(ValidationInput):
    object_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    category: Optional[Label] = None

    @model_validator(mode="after")
    def has_selector(self):
        if self.object_id is None and self.zone_id is None and self.category is None:
            raise ValueError("Select an object, zone, or category")
        return self


class ClearanceZone(ValidationInput):
    zone_footprint_id: UUID


class ClearanceRequirement(ValidationInput):
    id: Label
    minimum_m: Annotated[FiniteFloat, Field(strict=True, gt=0, le=1e6)]
    subject: ClearanceSelector
    against: Union[ClearanceSelector, ClearanceZone]

    @model_validator(mode="after")
    def distinct_objects(self):
        if (isinstance(self.against, ClearanceSelector) and self.subject.object_id is not None
                and self.subject.object_id == self.against.object_id):
            raise ValueError("Clearance must compare different objects")
        return self


class ValidationConfig(ValidationInput):
    rules: dict[RuleId, RuleOptions] = Field(default_factory=dict)
    collision: CollisionOptions = Field(default_factory=CollisionOptions)
    exclusive_zone_pairs: Optional[list[EntityPair]] = Field(default=None, max_length=500)
    require_zone_assignment: Optional[Annotated[bool, Field(strict=True)]] = None
    clearances: Optional[list[ClearanceRequirement]] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def unique_requirements(self):
        if self.clearances is not None and len({item.id for item in self.clearances}) != len(self.clearances):
            raise ValueError("Clearance requirement IDs must be unique")
        return self


class ValidationReportRequest(ValidationInput):
    config: ValidationConfig = Field(default_factory=ValidationConfig)
    plan_revision: Optional[Revision] = None
    runtime_revision: Optional[Revision] = None
    severity: Optional[Severity] = None
    category: Optional[Category] = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class RuleDescriptor(BaseModel):
    id: RuleId
    name: str
    category: Category
    default_severity: Severity
    severity_override: Optional[Severity]
    description: str
    complete: bool


class FindingEntity(BaseModel):
    id: UUID
    type: Literal["object", "group", "zone", "layer"]
    name: str
    object_type: Optional[str] = None


class FindingLocation(BaseModel):
    anchor_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    minimum_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    maximum_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class FindingMeasurement(BaseModel):
    measured: Optional[FiniteFloat] = None
    expected: Optional[FiniteFloat] = None
    shortfall: Optional[FiniteFloat] = None
    unit: Literal["m", "m²", "m³", "revision"]


class ValidationFinding(BaseModel):
    id: UUID
    rule_id: RuleId
    rule_name: str
    category: Category
    code: str
    severity: Severity
    blocking: bool
    title: str
    explanation: str
    resolution: str
    entities: list[FindingEntity]
    zone_ids: list[UUID]
    location: Optional[FindingLocation]
    measurement: Optional[FindingMeasurement]
    metadata: dict[str, Any]


class ValidationSummary(BaseModel):
    total: int
    errors: int
    warnings: int
    info: int
    blocking: int


class ValidationReport(BaseModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    scene_id: UUID
    scene_revision: int
    checked_at: datetime
    duration_ms: FiniteFloat
    configuration_fingerprint: str
    findings_fingerprint: str
    status: Literal["valid", "valid_with_findings", "invalid", "incomplete"]
    complete: bool
    summary: ValidationSummary
    rules_executed: list[RuleDescriptor]
    findings: list[ValidationFinding]
    filtered_total: int
    offset: int
    limit: int
