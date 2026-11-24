from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.production import NamedInput, Version
from app.schemas.production_bom import BOMVersion, PositiveQuantity, PriceAdjustments
from app.schemas.production_materials import Currency, Rate
from app.schemas.spatial import SpatialInput
from app.services.production_costing import CostCategory, CostUnit


Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=28, decimal_places=4)]
Precision = Annotated[int, Field(ge=0, le=4, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]
Label = Annotated[str, Field(min_length=1, max_length=200)]
Days = Annotated[int, Field(ge=0, le=3650, strict=True)]
SupplierStatus = Literal["active", "inactive", "archived"]
ProcurementStatus = Literal["requested", "sourcing", "approved", "ordered", "delivered", "cancelled"]


class CommercialInput(SpatialInput):
    @field_validator(
        "note", "location", "payment_terms", "coverage", "commercial_terms", "contact_name", "phone",
        "business_name", "description", "reference", "specification", "finish", "delivery_details", "storage_details",
        "rationale", "specification_comparison", "quantity_impact", "visual_impact", "technical_impact",
        "production_impact", "lead_time_impact", "risks", "evidence_reference", check_fields=False,
    )
    @classmethod
    def validate_commercial_text(cls, value):
        return cls.valid_label(value).strip() if value is not None else None


class BudgetWrite(CommercialInput):
    expected_version: BOMVersion
    currency: Currency
    currency_precision: Precision = 2
    amount: Optional[Money] = None
    allocations: dict[CostCategory, Money] = Field(default_factory=dict)
    bom_ids: list[UUID] = Field(default_factory=list, max_length=100)
    quote_ids: list[UUID] = Field(default_factory=list, max_length=100)
    note: Optional[Text] = None

    @model_validator(mode="after")
    def sources_unique(self):
        if len(set(self.bom_ids)) != len(self.bom_ids) or len(set(self.quote_ids)) != len(self.quote_ids):
            raise ValueError("Budget sources must be unique.")
        return self


class ApproveVersion(CommercialInput):
    expected_version: Version
    note: Optional[Text] = None


class BudgetRead(BaseModel):
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    version: int
    latest: bool
    currency: str
    currency_precision: int
    amount: Decimal
    unallocated: Decimal
    allocations: dict[CostCategory, Decimal]
    sources: dict
    categories: dict
    summary: dict
    approved_at: Optional[datetime]
    approved_by_user_id: Optional[UUID]
    created_at: datetime
    created_by_user_id: Optional[UUID]
    stale: bool
    stale_reasons: list[str]


class SupplierWrite(NamedInput, CommercialInput):
    business_name: Optional[Label] = None
    contact_name: Optional[Label] = None
    email: Optional[Annotated[str, Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]] = None
    phone: Optional[Label] = None
    categories: list[Label] = Field(default_factory=list, max_length=50)
    capabilities: list[Label] = Field(default_factory=list, max_length=50)
    location: Optional[Label] = None
    coverage: Optional[Text] = None
    payment_terms: Optional[Text] = None
    commercial_terms: Optional[Text] = None
    status: SupplierStatus = "active"

    @field_validator("categories", "capabilities")
    @classmethod
    def validate_list(cls, values):
        return [cls.valid_label(value).strip() for value in values]


class SupplierUpdate(SupplierWrite):
    expected_version: Version


class SupplierRead(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    status: SupplierStatus
    version: int
    details: dict
    created_at: datetime
    updated_at: datetime
    last_edited_by_user_id: Optional[UUID]


class OfferingWrite(NamedInput, CommercialInput):
    kind: Literal["material", "component", "service"]
    material_id: Optional[UUID] = None
    definition_id: Optional[UUID] = None
    description: Optional[Text] = None
    unit: Optional[CostUnit] = None
    minimum_quantity: Optional[PositiveQuantity] = None
    lead_time_days: Optional[Days] = None
    payment_terms: Optional[Text] = None
    commercial_terms: Optional[Text] = None
    active: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_offering(self):
        if (self.material_id and self.kind != "material") or (self.definition_id and self.kind != "component"):
            raise ValueError("Choose the catalogue reference matching the offering kind.")
        if self.minimum_quantity is not None and (
            self.unit is None or (self.unit == "each" and self.minimum_quantity != self.minimum_quantity.to_integral_value())
        ):
            raise ValueError("Minimum order quantity requires a unit and whole counts.")
        return self


class OfferingUpdate(OfferingWrite):
    expected_version: Version


class OfferingRead(BaseModel):
    id: UUID
    supplier_id: UUID
    name: str
    kind: Literal["material", "component", "service"]
    active: bool
    version: int
    material_id: Optional[UUID]
    definition_id: Optional[UUID]
    details: dict
    created_at: datetime
    updated_at: datetime
    last_edited_by_user_id: Optional[UUID]


class ProjectSupplierWrite(CommercialInput):
    expected_version: BOMVersion
    active: bool = Field(default=True, strict=True)
    note: Optional[Text] = None


class ProjectSupplierRead(BaseModel):
    project_id: UUID
    supplier_id: UUID
    active: bool
    version: int
    note: Optional[str]
    history: list[dict]
    supplier: dict


class QuoteLine(PriceAdjustments, CommercialInput):
    description: Text
    bom_line_id: Optional[UUID] = None
    offering_id: Optional[UUID] = None
    material_id: Optional[UUID] = None
    definition_id: Optional[UUID] = None
    specification: Optional[Text] = None
    finish: Optional[Text] = None
    quantity: Optional[PositiveQuantity] = None
    unit: Optional[CostUnit] = None
    unit_rate: Optional[Rate] = None
    supplier_total: Optional[Money] = None
    delivery_amount: Optional[Money] = None

    @model_validator(mode="after")
    def validate_count(self):
        if self.unit == "each" and self.quantity is not None and self.quantity != self.quantity.to_integral_value():
            raise ValueError("Quoted count must be a whole number.")
        return self


class QuoteWrite(CommercialInput):
    expected_version: BOMVersion
    bom_id: UUID
    supplier_id: UUID
    reference: str = Field(min_length=1, max_length=160)
    quoted_on: date
    valid_until: Optional[date] = None
    currency: Currency
    lead_time_days: Optional[Days] = None
    payment_terms: Optional[Text] = None
    delivery_charge: Optional[Money] = None
    supplier_total: Optional[Money] = None
    attachment_id: Optional[UUID] = None
    lines: list[QuoteLine] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_header(self):
        if self.valid_until is not None and self.valid_until < self.quoted_on:
            raise ValueError("Quotation validity must end on or after the quotation date.")
        ids = [row.bom_line_id for row in self.lines if row.bom_line_id is not None]
        if len(set(ids)) != len(ids):
            raise ValueError("A BOM requirement may be quoted once per quotation.")
        return self


class QuoteDecision(ApproveVersion):
    decision: Literal["approved", "rejected"]


QuoteStatus = Literal["received", "approved", "rejected"]


class QuoteRead(BaseModel):
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    bom_id: UUID
    supplier_id: UUID
    attachment_id: Optional[UUID]
    reference: str
    version: int
    status: QuoteStatus
    latest: bool
    expired: bool
    stale: bool
    stale_reasons: list[str]
    quoted_on: date
    valid_until: Optional[date]
    currency: str
    currency_precision: int
    details: dict
    lines: list[dict]
    audit: list[dict]
    created_at: datetime
    created_by_user_id: Optional[UUID]


class QuoteComparison(BaseModel):
    bom_id: UUID
    currency: str
    currency_precision: int
    quotes: list[QuoteRead]


class ProcurementGenerate(CommercialInput):
    bom_id: UUID
    line_ids: Optional[list[UUID]] = Field(default=None, min_length=1, max_length=100)
    required_delivery_date: Optional[date] = None
    delivery_details: Optional[Text] = None
    storage_details: Optional[Text] = None

    @field_validator("line_ids")
    @classmethod
    def unique_lines(cls, values):
        if values is not None and len(values) != len(set(values)):
            raise ValueError("BOM line IDs must be unique.")
        return values


class ProcurementSelection(CommercialInput):
    expected_version: Version
    preferred_supplier_id: Optional[UUID] = None
    alternative_supplier_ids: list[UUID] = Field(default_factory=list, max_length=50)
    selected_supplier_id: Optional[UUID] = None
    quotation_id: Optional[UUID] = None
    accept_quantity_difference: bool = Field(default=False, strict=True)
    required_delivery_date: Optional[date] = None
    delivery_details: Optional[Text] = None
    storage_details: Optional[Text] = None

    @model_validator(mode="after")
    def valid_suppliers(self):
        if len(set(self.alternative_supplier_ids)) != len(self.alternative_supplier_ids):
            raise ValueError("Alternative suppliers must be unique.")
        if self.preferred_supplier_id is not None and self.preferred_supplier_id in self.alternative_supplier_ids:
            raise ValueError("The preferred supplier cannot also be an alternative.")
        if self.quotation_id is not None and self.selected_supplier_id is None:
            raise ValueError("Select a supplier and quotation together.")
        return self


class ProcurementTransition(ApproveVersion):
    status: Literal["approved", "ordered", "delivered", "cancelled"]
    reference: Optional[Label] = None
    recorded_date: Optional[date] = None

    @model_validator(mode="after")
    def documentation(self):
        if self.status in ("ordered", "delivered") and (self.reference is None or self.recorded_date is None):
            raise ValueError("Record the existing order or delivery reference and date.")
        return self


class ProcurementRead(BaseModel):
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    bom_line_id: UUID
    selected_supplier_id: Optional[UUID]
    quotation_id: Optional[UUID]
    status: ProcurementStatus
    version: int
    requirement: dict
    selection: dict
    approval: Optional[dict]
    audit: list[dict]
    stale: bool
    stale_reasons: list[str]
    issues: list[str]
    created_at: datetime
    updated_at: datetime


class ProposalWrite(CommercialInput):
    expected_procurement_version: Version
    quotation_id: UUID
    design_artifact_id: Optional[UUID] = None
    rationale: Text
    specification_comparison: Text
    quantity_impact: Text
    visual_impact: Text
    technical_impact: Text
    production_impact: Text
    lead_time_impact: Text
    risks: Text
    evidence_reference: Optional[Text] = None


class ProposalDecision(ApproveVersion):
    expected_procurement_version: Version
    decision: Literal["approved", "rejected", "applied"]
    replaces_proposal_id: Optional[UUID] = None

    @model_validator(mode="after")
    def validate_replacement(self):
        if self.replaces_proposal_id is not None and self.decision != "applied":
            raise ValueError("A proposal can only replace an existing substitution when applied.")
        return self


class ProposalRead(BaseModel):
    id: UUID
    procurement_id: UUID
    quotation_id: UUID
    material_id: Optional[UUID]
    definition_id: Optional[UUID]
    design_artifact_id: Optional[UUID]
    status: Literal["proposed", "approved", "rejected", "applied"]
    version: int
    proposal: dict
    audit: list[dict]
    stale: bool
    stale_reasons: list[str]
    created_at: datetime
