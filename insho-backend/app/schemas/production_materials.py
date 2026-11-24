from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.spatial import SpatialInput
from app.services.production_costing import MaterialUnit


Currency = Annotated[str, Field(pattern=r"^[A-Za-z]{3}$")]
Rate = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=24, decimal_places=6)]
Allowance = Annotated[Decimal, Field(ge=0, le=1000, allow_inf_nan=False, decimal_places=6)]
MaterialStatus = Literal["active", "unavailable", "archived"]


class MaterialDetails(SpatialInput):
    specification: Optional[str] = Field(default=None, min_length=1, max_length=16000)
    finish: Optional[str] = Field(default=None, min_length=1, max_length=300)
    supplier_reference: Optional[str] = Field(default=None, min_length=1, max_length=300)
    source_reference: Optional[str] = Field(default=None, min_length=1, max_length=1000)

    @field_validator("specification", "finish", "supplier_reference", "source_reference")
    @classmethod
    def validate_text(cls, value):
        return cls.valid_label(value) if value is not None else None


class MaterialRates(MaterialDetails):
    unit_rate: Optional[Rate] = None
    currency: Optional[Currency] = None
    allowance_percent: Optional[Allowance] = None

    @model_validator(mode="after")
    def matching_currency(self):
        if (self.unit_rate is None) != (self.currency is None):
            raise ValueError("Supply a rate and currency together.")
        if "currency" in self.model_fields_set and "unit_rate" not in self.model_fields_set:
            raise ValueError("Supply the rate when overriding its currency.")
        return self


class MaterialDefinitionInput(MaterialRates):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=4000)
    unit: MaterialUnit
    allowance_percent: Allowance = Decimal(0)
    status: MaterialStatus = "active"


class MaterialDefinitionUpdate(MaterialDefinitionInput):
    expected_version: int = Field(gt=0, le=2147483647, strict=True)


class MaterialDefinitionRead(BaseModel):
    id: UUID
    organization_id: UUID
    version: int
    current_version: int
    name: str
    category: str
    description: Optional[str]
    unit: MaterialUnit
    status: MaterialStatus
    unit_rate: Optional[Decimal]
    currency: Optional[str]
    allowance_percent: Decimal
    specification: Optional[str]
    finish: Optional[str]
    supplier_reference: Optional[str]
    source_reference: Optional[str]
    entered_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime
    revised_at: datetime


class MaterialAssignmentRead(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    version: int
    definition_id: Optional[UUID]
    overrides: dict
    effective: Optional[dict]
