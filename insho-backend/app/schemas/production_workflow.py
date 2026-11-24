from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from app.schemas.spatial import SpatialInput


WorkflowStatus = Literal[
    "draft", "design_approved", "technical_review", "production_ready", "in_fabrication", "on_site",
    "installed", "inspected", "live", "dismantling", "archived",
]
TaskKind = Literal[
    "fabrication", "rigging", "electrical", "installation", "transport", "storage", "floral_delivery",
    "floral_storage", "testing", "commissioning", "inspection", "dismantling",
]


class WorkflowTask(SpatialInput):
    id: UUID
    name: str = Field(min_length=1, max_length=160)
    kind: TaskKind
    item_id: Optional[UUID] = None
    supplier_id: Optional[UUID] = None
    depends_on: list[UUID] = Field(default_factory=list, max_length=100)
    duration_minutes: Optional[int] = Field(default=None, strict=True, gt=0, le=525600)
    window_start: Optional[AwareDatetime] = None
    window_end: Optional[AwareDatetime] = None
    notes: Optional[str] = Field(default=None, min_length=1, max_length=4000)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value):
        return cls.valid_label(value) if value is not None else None

    @model_validator(mode="after")
    def validate_window(self):
        if self.window_start is not None or self.window_end is not None:
            if self.window_start is None or self.window_end is None or self.window_start >= self.window_end:
                raise ValueError("Supply both window endpoints in chronological order with time zones.")
        return self


class WorkflowWrite(SpatialInput):
    expected_version: Annotated[int, Field(strict=True, ge=0, lt=2147483647)]


class WorkflowPlanWrite(WorkflowWrite):
    tasks: list[WorkflowTask] = Field(max_length=200)

    @model_validator(mode="after")
    def validate_sequence(self):
        earlier = set()
        for task in self.tasks:
            dependencies = set(task.depends_on)
            if task.id in earlier or len(dependencies) != len(task.depends_on) or not dependencies <= earlier:
                raise ValueError("Task IDs must be unique and dependencies must reference distinct earlier tasks.")
            earlier.add(task.id)
        return self


class WorkflowTransition(WorkflowWrite):
    status: WorkflowStatus
    evidence_reference: Optional[str] = Field(default=None, min_length=1, max_length=1000)

    @field_validator("evidence_reference")
    @classmethod
    def validate_evidence(cls, value):
        return cls.valid_label(value) if value is not None else None


class SheetReference(BaseModel):
    id: UUID
    version: int
    item_id: UUID


class ReadinessBasis(BaseModel):
    bom_id: UUID
    bom_version: int
    record_version: int
    scene_revision: int
    specifications: list[SheetReference]


class WorkflowRevisionRead(BaseModel):
    version: int
    status: WorkflowStatus
    tasks: list[WorkflowTask]
    readiness_basis: Optional[ReadinessBasis]
    evidence_reference: Optional[str]
    changed_by_user_id: Optional[UUID]
    changed_at: Optional[datetime]


class ReadinessIssue(BaseModel):
    code: str
    item_id: Optional[UUID] = None
    task_id: Optional[UUID] = None


class WorkflowRead(WorkflowRevisionRead):
    record_id: UUID
    scene_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    allowed_transitions: list[WorkflowStatus]
    readiness_blockers: list[ReadinessIssue]
    warnings: list[str]
    readiness_stale: bool
    critical_path: Optional[list[UUID]]
    critical_path_minutes: Optional[int]
