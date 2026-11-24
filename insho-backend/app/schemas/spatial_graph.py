"""Scene graph routes use typed references to canonical entities."""

from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.spatial import SpatialGroupCreate, SpatialInput, SpatialRelationshipResponse, SpatialTransform


class NodeRef(SpatialInput):
    type: Literal["object", "group", "zone"]
    id: UUID


class ControlRef(SpatialInput):
    type: Literal["object", "group", "zone", "layer"]
    id: UUID


class CreateGroup(SpatialInput):
    kind: Literal["create_group"]
    id: Optional[UUID] = None
    group: SpatialGroupCreate
    members: list[NodeRef] = Field(default_factory=list, max_length=500)


class UpdateGroup(SpatialInput):
    kind: Literal["update_group"]
    id: UUID
    name: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = None
    category: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] = None
    transform: Optional[SpatialTransform] = None
    properties: Optional[dict[str, Any]] = None


class GroupAction(SpatialInput):
    kind: Literal["delete_group", "ungroup", "duplicate_group"]
    id: UUID


class Reparent(SpatialInput):
    kind: Literal["reparent"]
    node: NodeRef
    parent: Optional[NodeRef]


class Reorder(SpatialInput):
    kind: Literal["reorder"]
    scope: Literal["siblings", "layer_members"] = "siblings"
    node: ControlRef
    before: Optional[ControlRef]


class SetVisibility(SpatialInput):
    kind: Literal["set_visibility"]
    nodes: list[ControlRef] = Field(min_length=1, max_length=500)
    visible: Optional[bool] = None


class SetLocked(SpatialInput):
    kind: Literal["set_locked"]
    nodes: list[ControlRef] = Field(min_length=1, max_length=500)
    locked: bool


class CreateLayer(SpatialInput):
    kind: Literal["create_layer"]
    id: Optional[UUID] = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    metadata: dict[str, Any] = Field(default_factory=dict)
    visible: bool = True


class UpdateLayer(SpatialInput):
    kind: Literal["update_layer"]
    id: UUID
    name: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]] = None
    metadata: Optional[dict[str, Any]] = None


class DeleteLayer(SpatialInput):
    kind: Literal["delete_layer"]
    id: UUID


class SetDefaultLayer(SpatialInput):
    kind: Literal["set_default_layer"]
    layer_id: Optional[UUID]


class AssignLayer(SpatialInput):
    kind: Literal["assign_layer"]
    nodes: list[NodeRef] = Field(min_length=1, max_length=500)
    layer_id: Optional[UUID]


GraphOperation = Annotated[
    Union[CreateGroup, UpdateGroup, GroupAction, Reparent, Reorder, SetVisibility, SetLocked,
          CreateLayer, UpdateLayer, DeleteLayer, AssignLayer, SetDefaultLayer],
    Field(discriminator="kind"),
]


class GraphChange(SpatialInput):
    base_revision: int = Field(ge=1)
    operations: list[GraphOperation] = Field(min_length=1, max_length=100)


class AssetRef(BaseModel):
    id: UUID
    asset_id: UUID
    role: str


class GraphNode(NodeRef):
    library_definition_id: Optional[UUID] = None
    library_definition_version: Optional[int] = None
    name: str
    category: str
    parent: Optional[NodeRef]
    children: list[NodeRef]
    order_index: int
    layer_id: Optional[UUID]
    layer_order_index: Optional[int]
    zone_id: Optional[UUID]
    visible: bool
    locked: bool
    effective_visible: bool
    effective_locked: bool
    status: str
    metadata: dict[str, Any]
    assets: list[AssetRef]
    created_at: datetime
    updated_at: datetime


class GraphLayer(BaseModel):
    id: UUID
    is_default: bool
    name: str
    order_index: int
    visible: bool
    locked: bool
    metadata: dict[str, Any]
    members: list[NodeRef]
    created_at: datetime
    updated_at: datetime


class SceneGraph(BaseModel):
    scene_id: UUID
    project_id: UUID
    scene_revision: int
    default_layer_id: Optional[UUID]
    roots: list[NodeRef]
    nodes: list[GraphNode]
    layers: list[GraphLayer]
    relationships: list[SpatialRelationshipResponse]


class NodeRelations(BaseModel):
    scene_revision: int
    node: GraphNode
    parent: Optional[NodeRef]
    children: list[NodeRef]
    ancestors: list[NodeRef]
    descendants: list[NodeRef]
    siblings: list[NodeRef]


class OperationResult(BaseModel):
    kind: str
    ids: list[UUID]


class GraphChangeResult(BaseModel):
    scene_revision: int
    results: list[OperationResult]
