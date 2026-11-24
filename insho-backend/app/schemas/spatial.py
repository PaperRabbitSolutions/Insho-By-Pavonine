from datetime import datetime
from decimal import Decimal
import json
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class SpatialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "category", "scene_type", "object_type", "relationship_type", check_fields=False)
    @classmethod
    def valid_label(cls, value):
        if value is not None and not value.strip():
            raise ValueError("A label must contain non-whitespace characters.")
        return cls.valid_storage_text(value)

    @field_validator("description", check_fields=False)
    @classmethod
    def valid_storage_text(cls, value):
        if value is not None:
            if "\x00" in value:
                raise ValueError("Text cannot contain null characters.")
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("Text must be valid Unicode.") from exc
        return value

    @field_validator("metadata", "properties", "appearance", "material_refs", "tags", "data", check_fields=False)
    @classmethod
    def valid_storage_json(cls, value):
        if value is None:
            return None
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError("Extension fields must contain finite JSON values and valid Unicode.") from exc
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and "\x00" in item:
                raise ValueError("Extension fields cannot contain null characters.")
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (tuple, list)):
                pending.extend(item)
        return value


class Vector3(SpatialInput):
    x: Decimal = Field(default=Decimal("0"), allow_inf_nan=False)
    y: Decimal = Field(default=Decimal("0"), allow_inf_nan=False)
    z: Decimal = Field(default=Decimal("0"), allow_inf_nan=False)


class Scale3(SpatialInput):
    x: Decimal = Field(default=Decimal("1"), gt=0, allow_inf_nan=False)
    y: Decimal = Field(default=Decimal("1"), gt=0, allow_inf_nan=False)
    z: Decimal = Field(default=Decimal("1"), gt=0, allow_inf_nan=False)


class SpatialTransform(SpatialInput):
    position: Vector3 = Field(default_factory=Vector3)
    rotation_deg: Vector3 = Field(default_factory=Vector3)
    scale: Scale3 = Field(default_factory=Scale3)


class SceneDimensions(SpatialInput):
    width_m: Decimal = Field(gt=0, allow_inf_nan=False)
    depth_m: Decimal = Field(gt=0, allow_inf_nan=False)
    height_m: Decimal = Field(gt=0, allow_inf_nan=False)


class ObjectDimensions(SceneDimensions):
    pass


class FootprintPoint(SpatialInput):
    x_m: Decimal = Field(allow_inf_nan=False)
    y_m: Decimal = Field(allow_inf_nan=False)


class SpatialSceneCreate(SpatialInput):
    event_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = None
    scene_type: str = Field(default="venue", min_length=1, max_length=60)
    display_unit: str = Field(default="m", pattern="^(m|cm|mm|ft|in)$")
    dimensions: SceneDimensions
    status: str = Field(default="draft", pattern="^(draft|active|archived)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpatialSceneUpdate(SpatialInput):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = None
    scene_type: Optional[str] = Field(default=None, min_length=1, max_length=60)
    display_unit: Optional[str] = Field(default=None, pattern="^(m|cm|mm|ft|in)$")
    dimensions: Optional[SceneDimensions] = None
    status: Optional[str] = Field(default=None, pattern="^(draft|active|archived)$")
    metadata: Optional[dict[str, Any]] = None


class SpatialSceneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    event_id: Optional[UUID]
    name: str
    description: Optional[str]
    scene_type: str
    display_unit: str
    coordinate_system: str
    dimensions: SceneDimensions
    status: str
    metadata: dict[str, Any]
    current_revision: int
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class SpatialZoneCreate(SpatialInput):
    parent_zone_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=60)
    description: Optional[str] = None
    transform: SpatialTransform = Field(default_factory=SpatialTransform)
    dimensions: Optional[SceneDimensions] = None
    footprint: Optional[list[FootprintPoint]] = None
    appearance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="active", pattern="^(active|hidden|archived)$")

    @field_validator("footprint")
    @classmethod
    def validate_footprint(cls, value):
        if value is not None and len(value) < 3:
            raise ValueError("A zone footprint needs at least three points")
        return value


class SpatialZoneUpdate(SpatialInput):
    parent_zone_id: Optional[UUID] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    category: Optional[str] = Field(default=None, min_length=1, max_length=60)
    description: Optional[str] = None
    transform: Optional[SpatialTransform] = None
    dimensions: Optional[SceneDimensions] = None
    footprint: Optional[list[FootprintPoint]] = None
    appearance: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    status: Optional[str] = Field(default=None, pattern="^(active|hidden|archived)$")

    @field_validator("footprint")
    @classmethod
    def validate_footprint(cls, value):
        if value is not None and len(value) < 3:
            raise ValueError("A zone footprint needs at least three points")
        return value


class SpatialZoneResponse(BaseModel):
    order_index: int
    is_locked: bool
    id: UUID
    scene_id: UUID
    parent_zone_id: Optional[UUID]
    name: str
    category: str
    description: Optional[str]
    transform: SpatialTransform
    dimensions: Optional[SceneDimensions]
    footprint: Optional[list[FootprintPoint]]
    appearance: dict[str, Any]
    metadata: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class SpatialObjectCreate(SpatialInput):
    layer_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    parent_object_id: Optional[UUID] = None
    object_type: str = Field(default="custom", min_length=1, max_length=80)
    category: str = Field(default="custom", min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    transform: SpatialTransform = Field(default_factory=SpatialTransform)
    dimensions: ObjectDimensions
    appearance: dict[str, Any] = Field(default_factory=dict)
    material_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    properties: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=100)
    status: str = Field(default="active", pattern="^(active|hidden|archived)$")


class SpatialGroupCreate(SpatialInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    category: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = "group"
    layer_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    parent_object_id: Optional[UUID] = None
    transform: SpatialTransform = Field(default_factory=SpatialTransform)
    appearance: dict[str, Any] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list, max_length=100)
    status: str = Field(default="active", pattern="^(active|hidden|archived)$")


class SpatialObjectUpdate(SpatialInput):
    layer_id: Optional[UUID] = None
    zone_id: Optional[UUID] = None
    parent_object_id: Optional[UUID] = None
    object_type: Optional[str] = Field(default=None, min_length=1, max_length=80)
    category: Optional[str] = Field(default=None, min_length=1, max_length=80)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    transform: Optional[SpatialTransform] = None
    dimensions: Optional[ObjectDimensions] = None
    appearance: Optional[dict[str, Any]] = None
    material_refs: Optional[list[dict[str, Any]]] = Field(default=None, max_length=100)
    properties: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = Field(default=None, max_length=100)
    status: Optional[str] = Field(default=None, pattern="^(active|hidden|archived)$")


class SpatialObjectResponse(BaseModel):
    library_definition_id: Optional[UUID] = None
    library_definition_version: Optional[int] = None
    layer_id: Optional[UUID]
    layer_order_index: Optional[int]
    node_kind: Literal["object", "group"]
    order_index: int
    is_locked: bool
    id: UUID
    scene_id: UUID
    zone_id: Optional[UUID]
    parent_object_id: Optional[UUID]
    object_type: str
    category: str
    name: str
    transform: SpatialTransform
    dimensions: Optional[ObjectDimensions]
    effective_dimensions_m: Optional[SceneDimensions]
    appearance: dict[str, Any]
    material_refs: list[dict[str, Any]]
    properties: dict[str, Any]
    tags: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


class SpatialRelationshipCreate(SpatialInput):
    source_object_id: UUID
    target_object_id: UUID
    relationship_type: str = Field(min_length=1, max_length=60)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpatialRelationshipResponse(BaseModel):
    id: UUID
    scene_id: UUID
    source_object_id: UUID
    target_object_id: UUID
    relationship_type: str
    metadata: dict[str, Any]
    created_at: datetime


class SpatialAssetAttach(SpatialInput):
    asset_id: UUID
    role: str = Field(default="reference", pattern="^(model|preview|material|texture|reference)$")


class SpatialAssetResponse(BaseModel):
    id: UUID
    scene_id: UUID
    object_id: UUID
    asset_id: UUID
    role: str
    asset_metadata: dict[str, Any]
    created_at: datetime


class SpatialRevisionResponse(BaseModel):
    id: UUID
    scene_id: UUID
    revision_number: int
    changed_by_user_id: Optional[UUID]
    operation: str
    entity_type: str
    entity_id: UUID
    before_state: Optional[dict[str, Any]]
    after_state: Optional[dict[str, Any]]
    created_at: datetime


class SpatialHierarchyNode(BaseModel):
    id: UUID
    name: str
    node_type: str
    entity: dict[str, Any] = Field(default_factory=dict)
    children: list["SpatialHierarchyNode"] = Field(default_factory=list)


SpatialHierarchyNode.model_rebuild()
