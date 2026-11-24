"""Catalog, discovery, and canonical placement contracts."""

from datetime import datetime
from decimal import Decimal
import json
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.schemas.spatial import (
    FootprintPoint, ObjectDimensions, SpatialAssetAttach, SpatialInput, SpatialObjectResponse, SpatialTransform,
)
from app.schemas.spatial_runtime import Bounds3D


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Status = Literal["active", "archived"]
Scope = Literal["organization", "workspace", "project"]
Availability = Literal["none", "available", "unavailable"]


def _control_free(value):
    if value is not None and any(ord(char) < 32 for char in value):
        raise ValueError("Names, labels and tags cannot contain control characters.")
    return value


def _json_metadata(value):
    if value is not None:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError("Metadata must contain finite JSON values.") from exc
        pending = [value]
        while pending:
            current = pending.pop()
            if isinstance(current, str) and "\x00" in current:
                raise ValueError("Metadata cannot contain null characters.")
            if isinstance(current, dict):
                pending.extend(current.keys())
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)
        if len(encoded) > 65536:
            raise ValueError("Each metadata field is limited to 64 KiB.")
    return value


class CatalogGeometry(SpatialInput):
    type: Label = "box"
    radius_m: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    vertices: Optional[list[FootprintPoint]] = Field(default=None, min_length=3, max_length=512)
    start: Optional[FootprintPoint] = None
    end: Optional[FootprintPoint] = None

    @model_validator(mode="after")
    def shape_fields(self):
        allowed = {
            "box": set(), "rectangle": set(), "rect": set(), "point": set(),
            "circle": {"radius_m"}, "polygon": {"vertices"},
            "line": {"start", "end"}, "line_segment": {"start", "end"},
        }.get(self.type)
        fields = {field for field in self.model_fields_set - {"type"} if getattr(self, field) is not None}
        if allowed is not None and fields - allowed:
            raise ValueError("Geometry parameters must match the selected type.")
        return self

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value):
        return _control_free(value).lower()


class CatalogMetadata(SpatialInput):
    appearance: Optional[dict[str, Any]] = None
    material_refs: Optional[list[dict[str, Any]]] = Field(default=None, max_length=100)
    metadata: Optional[dict[str, Any]] = None

    @field_validator("appearance", "material_refs", "metadata")
    @classmethod
    def finite_metadata(cls, value):
        return _json_metadata(value)


class DefinitionFields(CatalogMetadata):
    name: Name
    description: Optional[str] = Field(default=None, max_length=4000)
    category: Label
    object_type: Label = "custom"
    tags: list[Label] = Field(default_factory=list, max_length=50)
    length_unit: Literal["m"] = "m"
    dimensions: ObjectDimensions
    default_transform: SpatialTransform = Field(default_factory=SpatialTransform)
    geometry: CatalogGeometry = Field(default_factory=CatalogGeometry)
    assets: list[SpatialAssetAttach] = Field(default_factory=list, max_length=50)

    @field_validator("name", "category", "object_type")
    @classmethod
    def clean_text(cls, value):
        return _control_free(value)

    @field_validator("description")
    @classmethod
    def valid_description(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("Description cannot contain a null character.")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        normalized = {_control_free(value).lower() for value in values}
        if any(len(tag) > 80 for tag in normalized):
            raise ValueError("Normalized tags cannot exceed 80 characters.")
        return sorted(normalized)


class DefinitionCreate(DefinitionFields):
    workspace_id: Optional[UUID] = None
    project_id: Optional[UUID] = None


class DefinitionUpdate(CatalogMetadata):
    expected_version: int = Field(ge=1)
    name: Optional[Name] = None
    description: Optional[str] = Field(default=None, max_length=4000)
    category: Optional[Label] = None
    object_type: Optional[Label] = None
    tags: Optional[list[Label]] = Field(default=None, max_length=50)
    length_unit: Literal["m"] = "m"
    dimensions: Optional[ObjectDimensions] = None
    default_transform: Optional[SpatialTransform] = None
    geometry: Optional[CatalogGeometry] = None
    assets: Optional[list[SpatialAssetAttach]] = Field(default=None, max_length=50)
    status: Optional[Status] = None


class DuplicateDefinition(SpatialInput):
    expected_version: int = Field(ge=1)
    name: Optional[Name] = None


class InstantiateDefinition(CatalogMetadata):
    definition_version: int = Field(ge=1)
    base_revision: int = Field(ge=1)
    name: Optional[Name] = None
    dimensions: Optional[ObjectDimensions] = None
    transform: Optional[SpatialTransform] = None
    parent_object_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    layer_id: Optional[UUID] = None
    status: Literal["active", "hidden"] = "active"


class CatalogFilters(SpatialInput):
    workspace_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    scope: Optional[Scope] = None
    status: Literal["active", "archived", "all"] = "active"
    q: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[Label] = None
    object_type: Optional[Label] = None
    tags: list[Label] = Field(default_factory=list, max_length=50)
    geometry_type: Optional[Label] = None
    plan_compatible: Optional[bool] = None
    asset_availability: Optional[Availability] = None
    min_width_m: Optional[Decimal] = Field(default=None, gt=0, le=99999999, allow_inf_nan=False)
    max_width_m: Optional[Decimal] = Field(default=None, gt=0, le=99999999, allow_inf_nan=False)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def ordered_width(self):
        if self.min_width_m is not None and self.max_width_m is not None and self.min_width_m > self.max_width_m:
            raise ValueError("Minimum width cannot exceed maximum width.")
        return self


class CatalogIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    field: Optional[str] = None
    asset_id: Optional[UUID] = None


class CatalogValidation(BaseModel):
    definition_id: Optional[UUID] = None
    status: Literal["valid", "valid_with_warnings", "invalid"]
    issues: list[CatalogIssue]
    plan_compatible: bool
    world_bounds: Optional[Bounds3D] = None


class DefinitionSummary(BaseModel):
    id: UUID
    organization_id: UUID
    project_id: Optional[UUID]
    workspace_id: Optional[UUID]
    scope: Scope
    name: str
    description: Optional[str]
    category: str
    object_type: str
    tags: list[str]
    dimensions: ObjectDimensions
    length_unit: Literal["m"] = "m"
    geometry_type: str
    asset_availability: Availability
    plan_compatible: bool
    status: Status
    version: int
    created_at: datetime
    updated_at: datetime


class DefinitionAsset(SpatialAssetAttach):
    available: bool


class DefinitionDetail(DefinitionSummary):
    default_transform: SpatialTransform
    geometry: CatalogGeometry
    appearance: dict[str, Any]
    material_refs: list[dict[str, Any]]
    metadata: dict[str, Any]
    assets: list[DefinitionAsset]
    validation: CatalogValidation


class CatalogPage(BaseModel):
    items: list[DefinitionSummary]
    offset: int
    limit: int
    has_more: bool


class CatalogFacet(BaseModel):
    value: str
    count: int


class CatalogFacets(BaseModel):
    categories: list[CatalogFacet]
    tags: list[CatalogFacet]


class LibraryPlacement(BaseModel):
    scene_revision: int
    object: SpatialObjectResponse
    validation: CatalogValidation
