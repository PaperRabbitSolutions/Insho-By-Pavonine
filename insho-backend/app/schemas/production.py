from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.production_materials import MaterialRates
from app.schemas.spatial import SpatialInput
from app.services.production_units import MeasurementKind


Version = Annotated[int, Field(strict=True, gt=0, le=2147483647)]
ItemKind = Literal["assembly", "component"]
GeometryTarget = Literal["object", "zone", "scene"]
LengthUnit = Literal["m", "cm", "mm", "ft", "in"]
LinearDimension = Literal["width", "length", "height"]


class LengthInput(SpatialInput):
    value: Decimal = Field(gt=0, allow_inf_nan=False, max_digits=30)
    unit: LengthUnit


class NamedInput(SpatialInput):
    name: str = Field(min_length=1, max_length=160)


class RecordCreate(NamedInput):
    supersedes_id: Optional[UUID] = None


class DescribedInput(NamedInput):
    description: Optional[str] = Field(default=None, max_length=4000)


class ExpectedVersion(SpatialInput):
    expected_version: Version


class SourceReview(ExpectedVersion):
    scene_revision: Version


class RecordUpdate(NamedInput, ExpectedVersion):
    pass


class ItemFields(DescribedInput):
    material_id: Optional[UUID] = None


class ItemCreate(ItemFields, ExpectedVersion):
    kind: ItemKind = "component"
    parent_item_id: Optional[UUID] = None
    spatial_object_id: Optional[UUID] = None
    spatial_zone_id: Optional[UUID] = None
    library_definition_id: Optional[UUID] = None


class ItemUpdate(ItemFields, ExpectedVersion):
    parent_item_id: Optional[UUID] = None


class MaterialWrite(DescribedInput, ExpectedVersion):
    definition_id: Optional[UUID] = None
    overrides: Optional[MaterialRates] = None


class TechnicalParameter(NamedInput):
    value: str = Field(min_length=1, max_length=2000)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=32)

    @field_validator("value", "unit")
    @classmethod
    def storage_text(cls, value):
        return cls.valid_label(value) if value is not None else None


class TechnicalSpecificationInput(SpatialInput):
    source: Literal["user_entered", "spatial_geometry"] = "user_entered"
    target: Optional[GeometryTarget] = None
    scene_revision: Version
    dimensions: dict[LinearDimension, LengthInput] = Field(default_factory=dict)
    finish: Optional[str] = Field(default=None, min_length=1, max_length=300)
    fabrication_notes: Optional[str] = Field(default=None, min_length=1, max_length=8000)
    installation_requirements: Optional[str] = Field(default=None, min_length=1, max_length=8000)
    parameters: list[TechnicalParameter] = Field(default_factory=list, max_length=50)
    quantity_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @field_validator("finish", "fabrication_notes", "installation_requirements")
    @classmethod
    def storage_text(cls, value):
        return cls.valid_label(value) if value is not None else None

    @model_validator(mode="after")
    def validate_source(self):
        if self.source == "spatial_geometry" and (self.target is None or self.dimensions):
            raise ValueError("A geometry suggestion requires a target and reads dimensions from the scene.")
        if len({entry.name.strip().casefold() for entry in self.parameters}) != len(self.parameters):
            raise ValueError("Technical parameter names must be unique.")
        if len(set(self.quantity_ids)) != len(self.quantity_ids):
            raise ValueError("Technical specification quantity references must be unique.")
        return self


class SpecificationWrite(NamedInput, ExpectedVersion):
    content: str = Field(min_length=1, max_length=16000)
    technical: Optional[TechnicalSpecificationInput] = None
    supersedes_id: Optional[UUID] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        return cls.valid_label(value)


class ManualSource(SpatialInput):
    type: Literal["user_entered"]


class GeometrySource(SpatialInput):
    type: Literal["spatial_geometry"]
    target: GeometryTarget
    scene_revision: Version


class LibrarySource(SpatialInput):
    type: Literal["object_library"]
    definition_version: Version


class AISource(SpatialInput):
    type: Literal["ai_suggestion"]
    recommendation_id: UUID
    review_id: UUID


class SpecificationSource(SpatialInput):
    type: Literal["technical_specification"]
    specification_id: UUID
    specification_version: Version


MeasurementSource = Annotated[
    Union[ManualSource, GeometrySource, LibrarySource, AISource, SpecificationSource],
    Field(discriminator="type"),
]


class MeasurementWrite(ExpectedVersion):
    source: MeasurementSource
    value: Optional[Decimal] = Field(default=None, allow_inf_nan=False, max_digits=40)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=8)
    replace_manual: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_supplied_value(self):
        if self.source.type in ("spatial_geometry", "object_library"):
            if self.value is not None or self.unit is not None:
                raise ValueError("Geometry and library measurements are read from their source.")
        elif self.value is None or self.unit is None:
            raise ValueError("This source requires a value and unit.")
        return self


class Mutation(BaseModel):
    id: UUID
    record_version: int


class MaterialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str]


class SpecificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    content: str
    version: int
    technical: Optional[dict] = None
    status: Literal["draft", "suggestion", "approved"] = "draft"
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    supersedes_id: Optional[UUID] = None
    superseded_by_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    approved_by_user_id: Optional[UUID] = None


class MeasurementRead(BaseModel):
    kind: MeasurementKind
    value: Decimal
    unit: str
    source: dict
    entered_by_user_id: Optional[UUID]
    stale: bool


class ItemRead(BaseModel):
    id: UUID
    kind: ItemKind
    name: str
    description: Optional[str]
    parent_item_id: Optional[UUID]
    material_id: Optional[UUID]
    spatial_object_id: Optional[UUID]
    spatial_zone_id: Optional[UUID]
    library_definition_id: Optional[UUID]
    source: dict
    reviewed_library_version: Optional[int]
    stale_reasons: list[str]
    specifications: list[SpecificationRead]
    measurements: list[MeasurementRead]


class AssemblyEntry(ItemRead):
    depth: int


class RecordRead(BaseModel):
    id: UUID
    scene_id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    supersedes_id: Optional[UUID]
    name: str
    status: Literal["draft", "approved"]
    version: int
    scene_revision: int
    current_scene_revision: int
    scene_stale: bool
    created_by_user_id: Optional[UUID]
    approved_by_user_id: Optional[UUID]
    approved_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class RecordDetail(RecordRead):
    stale: bool
    stale_reasons: list[str]
    items: list[ItemRead]
    materials: list[MaterialRead]
