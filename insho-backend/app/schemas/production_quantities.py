from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.production import GeometryTarget, LengthInput, SourceReview, Version
from app.schemas.spatial import SpatialInput


QuantityMethod = Literal["plan_area", "surface_area", "linear", "solid_volume", "object_count", "led_surface", "floral_stems"]
Plane = Literal["xy", "xz", "yz"]


class AllowanceRule(SpatialInput):
    allowance_percent: Decimal = Field(default=Decimal("0"), ge=0, le=1000, decimal_places=6, allow_inf_nan=False)


class PlanAreaRule(AllowanceRule):
    method: Literal["plan_area"]


class SurfaceAreaRule(AllowanceRule):
    method: Literal["surface_area"]
    face: Plane


class LinearRule(AllowanceRule):
    method: Literal["linear"]
    axis: Literal["x", "y", "z", "path"]


class VolumeRule(AllowanceRule):
    method: Literal["solid_volume"]


class CountRule(AllowanceRule):
    method: Literal["object_count"]


class Resolution(SpatialInput):
    width_px: Version
    height_px: Version


class LEDRule(SpatialInput):
    method: Literal["led_surface"]
    face: Plane
    pixel_pitch: LengthInput
    resolution: Optional[Resolution] = None


class FloralRule(AllowanceRule):
    method: Literal["floral_stems"]
    recipe_reference: str = Field(min_length=1, max_length=200)
    stems_per_arrangement: Version

    @field_validator("recipe_reference")
    @classmethod
    def validate_reference(cls, value):
        return cls.valid_label(value).strip()


QuantityRule = Annotated[
    Union[PlanAreaRule, SurfaceAreaRule, LinearRule, VolumeRule, CountRule, LEDRule, FloralRule],
    Field(discriminator="method"),
]


class QuantityRequest(SpatialInput):
    item_id: UUID
    target: GeometryTarget
    rule: QuantityRule


class CalculateQuantities(SourceReview):
    calculations: list[QuantityRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def no_duplicate_requests(self):
        keys = {(row.item_id, row.target, row.rule.method, getattr(row.rule, "face", getattr(row.rule, "axis", "")))
                for row in self.calculations}
        if len(keys) != len(self.calculations):
            raise ValueError("Each quantity line may appear once per request.")
        return self


class QuantityRead(BaseModel):
    id: UUID
    record_id: UUID
    item_id: UUID
    scene_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    method: QuantityMethod
    version: int
    engine_version: int
    scene_revision: int
    current_scene_revision: int
    source: dict
    formula: str
    parameters: dict
    inputs: dict
    outputs: dict
    value: Optional[Decimal]
    unit: Literal["m", "m2", "m3", "each"]
    status: Literal["calculated", "invalid"]
    errors: list[dict[str, str]]
    stale: bool
    latest: bool
    approved: bool
    approved_at: Optional[datetime]
    approved_by_user_id: Optional[UUID]
    calculated_by_user_id: Optional[UUID]
    calculated_at: datetime


class CalculationResult(BaseModel):
    record_version: int
    quantities: list[QuantityRead]
