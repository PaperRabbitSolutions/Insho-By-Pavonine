from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.production import Version
from app.schemas.production_materials import Currency, Rate
from app.schemas.spatial import SpatialInput
from app.services.production_costing import CostBasis, CostCategory, CostUnit, MaterialUnit


PositiveQuantity = Annotated[Decimal, Field(gt=0, allow_inf_nan=False, max_digits=24, decimal_places=6)]
Percentage = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False, decimal_places=6)]
BOMVersion = Annotated[int, Field(ge=0, le=2147483647, strict=True)]


class PriceAdjustments(SpatialInput):
    discount_percent: Percentage = Decimal(0)
    tax_percent: Percentage = Decimal(0)


class Consumption(SpatialInput):
    quantity: PositiveQuantity
    unit: MaterialUnit
    per_quantity: PositiveQuantity
    per_unit: MaterialUnit


class GenerateLine(PriceAdjustments):
    type: Literal["generate"]
    quantity_id: UUID
    item_id: Optional[UUID] = None
    specification_id: Optional[UUID] = None
    consumption: Optional[Consumption] = None
    replace_manual: bool = Field(default=False, strict=True)


class ManualLine(PriceAdjustments):
    type: Literal["manual"]
    line_id: Optional[UUID] = None
    item_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=160)
    category: CostCategory
    basis: CostBasis
    quantity: PositiveQuantity
    unit: CostUnit
    unit_rate: Optional[Rate] = None
    source_reference: str = Field(min_length=1, max_length=1000)

    @field_validator("source_reference")
    @classmethod
    def validate_reference(cls, value):
        return cls.valid_label(value).strip()

    @model_validator(mode="after")
    def validate_count(self):
        if self.unit == "each" and self.quantity != self.quantity.to_integral_value():
            raise ValueError("A count must be a whole number.")
        return self


class RemoveLine(SpatialInput):
    type: Literal["remove"]
    line_id: UUID


BOMChange = Annotated[Union[GenerateLine, ManualLine, RemoveLine], Field(discriminator="type")]


class BOMWrite(SpatialInput):
    expected_record_version: Version
    expected_bom_version: BOMVersion
    scene_revision: Version
    currency: Currency
    currency_precision: int = Field(default=2, ge=0, le=4, strict=True)
    note: Optional[str] = Field(default=None, max_length=4000)
    changes: list[BOMChange] = Field(min_length=1, max_length=100)

    @field_validator("note")
    @classmethod
    def validate_note(cls, value):
        return cls.valid_storage_text(value)


class BOMApprove(SpatialInput):
    expected_record_version: Version
    expected_bom_version: Version
    scene_revision: Version


class BOMLineRead(BaseModel):
    id: UUID
    line_key: str
    name: str
    origin: Literal["generated", "manual"]
    basis: CostBasis
    category: CostCategory
    quantity: Optional[Decimal]
    unit: Optional[CostUnit]
    unit_rate: Optional[Decimal]
    unit_rate_currency: Optional[str]
    total: Optional[Decimal]
    inputs: dict
    cost: dict
    issues: list[dict[str, str]]
    stale_reasons: list[str]
    stale: bool
    entered_by_user_id: Optional[UUID]


class BOMRead(BaseModel):
    id: UUID
    record_id: UUID
    scene_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    version: int
    record_version: int
    current_record_version: int
    scene_revision: int
    current_scene_revision: int
    currency: str
    currency_precision: int
    note: Optional[str]
    latest: bool
    created_by_user_id: Optional[UUID]
    created_at: datetime
    approved_at: Optional[datetime]
    approved_by_user_id: Optional[UUID]


class CostSummary(BaseModel):
    basis: CostBasis
    priced_subtotal: Decimal
    total: Optional[Decimal]
    unpriced_lines: int
    stale_lines: int


class BOMDetail(BOMRead):
    stale: bool
    lines: list[BOMLineRead]
    costs: list[CostSummary]


class BOMCostRead(BOMRead):
    stale: bool
    costs: list[CostSummary]


class CurrencyCostSummary(CostSummary):
    currency: str
    currency_precision: int


class ProjectCostRead(BaseModel):
    project_id: UUID
    event_id: Optional[UUID]
    records: list[BOMCostRead]
    approved_records: list[BOMCostRead]
    totals: list[CurrencyCostSummary]
    approved_totals: list[CurrencyCostSummary]
    ambiguous_scene_ids: list[UUID]
    approved_ambiguous_scene_ids: list[UUID]
    unestimated_record_ids: list[UUID]
