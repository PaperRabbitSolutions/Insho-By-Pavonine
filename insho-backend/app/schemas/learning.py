"""Typed post-event records and reproducible learning report contracts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.services.production_costing import CostCategory, MaterialUnit

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=r"^[^\x00-\x1f]+$")]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=r"^[^\x00]+$")]
Currency = Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{3}$")]
Amount = Annotated[Decimal, Field(ge=0, max_digits=32, decimal_places=4)]
QuantityValue = Annotated[Decimal, Field(ge=0, max_digits=24, decimal_places=6)]
ActualKind = Literal["cost", "quantity", "labour", "installation", "wastage", "vendor", "client_feedback",
                     "concept_decision", "weather_incident", "production_incident", "delay", "change_order", "final_media"]
Metric = Literal["cost", "quantity", "labour", "installation", "wastage"]
OutcomeStatus = Literal["draft", "verified"]
RecordOrigin = Literal["observed", "reported"]
ReportKind = Literal["reconciliation", "insights"]
Revision = Annotated[int, Field(strict=True, ge=1, lt=2147483647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Input):
    kind: Literal["actual", "reconciliation", "design_version", "design_audit", "project_memory", "presentation_checkpoint",
                  "presentation", "design_artifact", "design_element", "budget", "bom_line", "workflow_task", "asset", "supplier",
                  "material", "component", "recorded_plan", "venue", "scene", "scene_object"]
    id: UUID
    revision: Optional[int] = None
    parent_id: Optional[UUID] = None


class WorkflowTaskRef(Input):
    record_id: UUID
    revision: Revision
    task_id: UUID


class CostBaseline(Input):
    kind: Literal["budget", "bom_line"]
    id: UUID


class Plan(Input):
    value: QuantityValue
    source_reference: Note


class Cause(Input):
    observation: Note
    source_reference: Optional[Note] = None


class Window(Input):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("The end must be after the start.")
        return self


class MaterialAmount(Input):
    value: QuantityValue
    unit: MaterialUnit

    @model_validator(mode="after")
    def whole_items(self):
        if self.unit == "each" and self.value != self.value.to_integral_value():
            raise ValueError("Counts in each must be whole numbers.")
        return self


class Quantitative(Input):
    coverage: Literal["partial", "complete"] = "partial"


class Cost(Quantitative):
    kind: Literal["cost"]
    baseline: CostBaseline
    category: Union[CostCategory, Literal["budget_total"]]
    amount: Amount
    currency: Currency
    supplier_id: Optional[UUID] = None
    invoice_reference: Optional[Label] = None
    spent_on: date


class Quantity(Quantitative):
    kind: Literal["quantity"]
    bom_line_id: UUID
    material_id: Optional[UUID] = None
    component_id: Optional[UUID] = None
    consumed: MaterialAmount
    wastage: Optional[MaterialAmount] = None


class Labour(Quantitative):
    kind: Literal["labour"]
    activity: WorkflowTaskRef
    category: Label
    supplier_id: Optional[UUID] = None
    team_reference: Optional[Label] = None
    worker_count: Optional[int] = Field(default=None, strict=True, gt=0, le=1_000_000)
    work_window: Window
    hours: QuantityValue
    hours_basis: Literal["team_total", "per_worker"]
    planned_team_hours: Optional[Plan] = None

    @model_validator(mode="after")
    def require_count(self):
        if self.hours_basis == "per_worker" and self.worker_count is None:
            raise ValueError("Per-worker hours require the number of workers covered by that rate.")
        return self


class Installation(Quantitative):
    kind: Literal["installation"]
    activity: WorkflowTaskRef
    installed: Window
    reason: Optional[Cause] = None


class Wastage(Quantitative):
    kind: Literal["wastage"]
    material_id: UUID
    activity: Optional[WorkflowTaskRef] = None
    wasted: MaterialAmount
    expected: Optional[Plan] = None
    expected_unit: Optional[MaterialUnit] = None
    category: Label
    reason: Optional[Cause] = None

    @model_validator(mode="after")
    def planned_unit(self):
        if (self.expected is None) != (self.expected_unit is None):
            raise ValueError("An expected wastage value and its unit must be supplied together.")
        if self.expected and self.expected_unit == "each" and self.expected.value != self.expected.value.to_integral_value():
            raise ValueError("Counts in each must be whole numbers.")
        return self


class Vendor(Input):
    kind: Literal["vendor"]
    supplier_id: UUID
    period: Window
    dimension: Literal["delivery_reliability", "delivery_timing", "material_quality", "specification_compliance",
                       "execution_quality", "communication", "responsiveness", "installation_performance", "issue_resolution"]
    outcome: Literal["met", "missed", "mixed", "observation"]
    observation: Note
    incident_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ClientFeedback(Input):
    kind: Literal["client_feedback"]
    category: Label
    feedback_source: Note
    written_feedback: Optional[Note] = None
    rating: Optional[QuantityValue] = None
    rating_scale_max: Optional[QuantityValue] = None
    element_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def rating_basis(self):
        if self.rating is not None:
            if not self.rating_scale_max or self.rating > self.rating_scale_max:
                raise ValueError("Provide the explicitly reported rating scale maximum.")
        elif self.rating_scale_max is not None:
            raise ValueError("A rating scale requires an explicitly reported rating.")
        if self.rating is None and self.written_feedback is None:
            raise ValueError("Provide written feedback or an explicitly reported rating.")
        return self


class ConceptDecision(Input):
    kind: Literal["concept_decision"]
    checkpoint_id: UUID
    artifact_id: Optional[UUID] = None
    memory_item_id: Optional[UUID] = None


class WeatherIncident(Input):
    kind: Literal["weather_incident"]
    category: Label
    occurred_at: AwareDatetime
    venue_id: UUID
    impact: Note
    activities: list[WorkflowTaskRef] = Field(default_factory=list, max_length=20)
    resolution: Optional[Note] = None


class ProductionIncident(Input):
    kind: Literal["production_incident"]
    category: Label
    description: Note
    impact: Literal["minor", "disruptive", "major", "unclassified"] = "unclassified"
    occurred_at: AwareDatetime
    reported_at: AwareDatetime
    activities: list[WorkflowTaskRef] = Field(default_factory=list, max_length=20)
    cause: Optional[Cause] = None
    resolution: Optional[Note] = None

    @model_validator(mode="after")
    def sent_after_occurrence(self):
        if self.reported_at < self.occurred_at:
            raise ValueError("Reporting time must be on or after the incident.")
        return self


class Delay(Input):
    kind: Literal["delay"]
    activity: WorkflowTaskRef
    actual: Window
    impact: Note
    reason: Optional[Cause] = None


class ChangeOrder(Input):
    kind: Literal["change_order"]
    original_scope: Note
    changed_scope: Note
    requested_at: AwareDatetime
    requester_reference: Label
    reason: Cause
    reason_category: Optional[Literal["scope", "site_conditions", "availability", "client_request", "design_revision", "other"]] = None
    approval_status: Literal["requested", "approved", "rejected", "withdrawn"]
    approval_reference: Optional[Note] = None
    cost_impact: Optional[Annotated[Decimal, Field(max_digits=32, decimal_places=4)]] = None
    currency: Optional[Currency] = None
    quantity_impact: Optional[Annotated[Decimal, Field(max_digits=24, decimal_places=6)]] = None
    quantity_unit: Optional[MaterialUnit] = None
    schedule_impact_minutes: Optional[int] = Field(default=None, strict=True, ge=-525600, le=525600)
    activities: list[WorkflowTaskRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def impact_basis(self):
        if (self.cost_impact is None) != (self.currency is None):
            raise ValueError("Cost impact requires a currency.")
        if (self.quantity_impact is None) != (self.quantity_unit is None):
            raise ValueError("Quantity impact requires a unit.")
        if self.quantity_unit == "each" and self.quantity_impact != self.quantity_impact.to_integral_value():
            raise ValueError("Counts in each must be whole numbers.")
        if self.approval_status in {"approved", "rejected"} and self.approval_reference is None:
            raise ValueError("A final approval status requires the decision reference.")
        return self


class FinalMedia(Input):
    kind: Literal["final_media"]
    asset_id: UUID
    captured_at: Optional[AwareDatetime] = None
    scene_id: Optional[UUID] = None
    scene_object_id: Optional[UUID] = None
    description: Note

    @model_validator(mode="after")
    def object_scene(self):
        if self.scene_object_id and not self.scene_id:
            raise ValueError("Specify the captured scene for a related object.")
        return self


Outcome = Annotated[
    Union[Cost, Quantity, Labour, Installation, Wastage, Vendor, ClientFeedback, ConceptDecision,
          WeatherIncident, ProductionIncident, Delay, ChangeOrder, FinalMedia], Field(discriminator="kind")
]


class ActualInput(Input):
    origin: RecordOrigin
    source_reference: Optional[Note] = None
    asset_ids: list[UUID] = Field(default_factory=list, max_length=20)
    audit_id: Optional[UUID] = None
    data: Outcome


class ActualCreate(ActualInput):
    event_id: UUID
    design_version_id: Optional[UUID] = None
    supersedes_id: Optional[UUID] = None


class ActualEdit(ActualInput):
    expected_revision: Revision


class ActualVerify(Input):
    expected_revision: Revision
    review_note: Note


class EstimateOnly(Input):
    metric: Literal["cost", "quantity", "installation"]
    cost: Optional[CostBaseline] = None
    bom_line_id: Optional[UUID] = None
    activity: Optional[WorkflowTaskRef] = None

    @model_validator(mode="after")
    def matches_metric(self):
        fields = {"cost": self.cost, "quantity": self.bom_line_id, "installation": self.activity}
        if {key for key, value in fields.items() if value is not None} != {self.metric}:
            raise ValueError("Select exactly the baseline reference for the chosen metric.")
        return self


class Reconcile(Input):
    event_id: UUID
    design_version_id: UUID
    estimates: list[EstimateOnly] = Field(default_factory=list, max_length=100)


class InsightRequest(Input):
    event_ids: list[UUID] = Field(min_length=1, max_length=20)
    reconciliation_ids: list[UUID] = Field(default_factory=list, max_length=20)
    ai_assisted: Annotated[bool, Field(strict=True)] = False
    idempotency_key: Optional[UUID] = None


class Disposition(Input):
    decision: Literal["accepted_for_review", "dismissed", "deferred"]
    note: Note


class ActualRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    event_id: UUID
    design_version_id: Optional[UUID]
    snapshot_id: Optional[UUID]
    supersedes_id: Optional[UUID]
    status: OutcomeStatus
    kind: ActualKind
    revision: int
    recorded_by_id: Optional[UUID]
    verified_by_id: Optional[UUID]
    created_at: datetime
    occurred_on: date
    verified_at: Optional[datetime]
    review_note: Optional[str]
    origin: RecordOrigin
    source_reference: Optional[str]
    data: dict
    derived: dict
    sources: list[Source]
    available_assets: list[UUID]


class Comparison(BaseModel):
    metric: Metric
    key: str
    category: str
    status: Literal["compared", "missing_estimate", "missing_actual", "unverified_actual", "partial_actual",
                    "incompatible_unit", "currency_mismatch", "ambiguous_actual"]
    estimate: Optional[Decimal]
    actual: Optional[Decimal]
    actual_in_estimate_units: Optional[Decimal]
    difference: Optional[Decimal]
    absolute_difference: Optional[Decimal]
    percentage_difference: Optional[Decimal]
    estimate_unit: Optional[str]
    actual_unit: Optional[str]
    conversion_method: Optional[str]
    comparison_group: str
    actual_ids: list[UUID]
    verified_actual_id: Optional[UUID] = None
    sources: list[Source]


class Insight(BaseModel):
    id: UUID
    project_id: UUID
    created_at: datetime
    category: str
    context: str
    status: Literal["review_required"] = "review_required"
    origin: Literal["derived"] = "derived"
    sample_count: int
    eligible_event_count: int
    period_start: date
    period_end: date
    event_ids: list[UUID]
    sources: list[Source]
    method: str
    coverage: str
    confidence: Literal["limited", "moderate"]
    observation: str
    recommended_action: str
    future_decision: str
    uncertainty: str
    human_review_required: Literal[True] = True
    ai_emphasis: Optional[Literal["review_estimate", "review_execution", "review_evidence"]] = None
    ai_origin: Optional[Literal["ai_inferred"]] = None
    ai_evidence_id: Optional[UUID] = None


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: ReportKind
    project_id: UUID
    event_id: Optional[UUID]
    event_ids: list[UUID]
    design_version_id: Optional[UUID]
    snapshot_id: Optional[UUID]
    created_at: datetime
    initiated_by_id: Optional[UUID]
    method_version: str
    input_fingerprint: str
    ai_execution_id: Optional[UUID]
    warnings: list[str]


class ReportDetail(ReportRead):
    comparisons: list[Comparison] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
    evidence: list[Source]


class DispositionRead(Disposition):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    report_id: UUID
    insight_id: UUID
    actor_id: Optional[UUID]
    created_at: datetime
